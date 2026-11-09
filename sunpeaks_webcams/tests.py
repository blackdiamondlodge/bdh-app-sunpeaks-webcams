from selenium import webdriver
from selenium.webdriver.common.by import By
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import tag
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import time
from datetime import datetime, timedelta, timezone
import re

from django.test import TestCase
from dateutil import parser as dt_parser
import pytz
from tests.selenium_helpers import get_chrome_options

import io
from unittest import mock

import requests
from django.test import SimpleTestCase
from PIL import Image

from sunpeaks_webcams import feed as feed_mod
from sunpeaks_webcams.feed import WebcamFeed, group_by_elevation, parse_page
from sunpeaks_webcams.views import check_for_new_webcams

T0 = 1_800_000_000
IMG_BASE = 'https://www.sunpeaksresort.com/sites/default/files/spr_website_data/webcams/'


def _cam_html(name, file, ts, elevation, updated='November 8th 8:55pm', location='Top of Lift'):
    src = f'/sites/default/files/spr_website_data/webcams/{file}' + (f'?timestamp={ts}' if ts else '')
    return f'''<div class="cam"><h2>{name}</h2><div class="image"><img src="{src}"></div>
<table><tr><td>Last Updated</td><td>{updated}</td></tr><tr><td>Location</td><td>{location}</td></tr>
<tr><td>Elevation</td><td>{elevation}</td></tr></table></div>'''


def _page(ts=T0):
    cams = [
        _cam_html('Crystal Bowl', 'a.jpg', ts, "2,080m (6,824')"),
        _cam_html('Sundance', 'b.jpg', ts + 5, "1,731m (5,679')"),
        _cam_html('Village Clock Tower', 'c.jpg', ts - 3, "Village Base, 1,255m (4,116')"),
    ]
    return f'<html><div id="webcams">{"".join(cams)}</div></html>'.encode()


class FakeResponse:
    def __init__(self, status=200, content=b'', headers=None):
        self.status_code = status
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers or {}))
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class ParsePageTests(SimpleTestCase):
    def test_parses_fields_and_derived_values(self):
        html = _page().replace(b'</div></html>', _cam_html('No Stamp', 'd.jpg', None, 'n/a').encode() + b'</div></html>')
        cams = parse_page(html)
        self.assertEqual([c['slug'] for c in cams], ['crystal-bowl', 'sundance', 'village-clock-tower', 'no-stamp'])
        crystal = cams[0]
        self.assertEqual(crystal['image_url'], f'{IMG_BASE}a.jpg?timestamp={T0}')
        self.assertEqual(crystal['timestamp'], str(T0))
        self.assertEqual(crystal['elevation_m'], 2080)
        self.assertEqual(crystal['elevation_label'], '2,080 m')
        self.assertEqual(crystal['time_label'], '8:55 PM')
        self.assertEqual(cams[2]['elevation_m'], 1255)
        self.assertIsNone(cams[3]['timestamp'])
        self.assertIsNone(cams[3]['elevation_m'])

    def test_skips_malformed_blocks_and_missing_container(self):
        html = b'<div id="webcams"><div class="cam"><p>broken</p></div>' + _cam_html('Ok', 'a.jpg', T0, '1,600m').encode() + b'</div>'
        self.assertEqual([c['camera_name'] for c in parse_page(html)], ['Ok'])
        self.assertEqual(parse_page(b'<html>maintenance</html>'), [])

    def test_group_by_elevation(self):
        cams = parse_page(_page()) + [{'camera_name': 'X', 'elevation_m': None}]
        bands = group_by_elevation(cams)
        self.assertEqual([b['name'] for b in bands], ['Alpine', 'Mid-mountain', 'Village', 'Other'])
        self.assertEqual(bands[0]['range'], '2,080 m')
        self.assertEqual(bands[3]['range'], '')

        mid = [{'camera_name': str(m), 'elevation_m': m} for m in (1600, 1731, 1672, 1600, 1500)]
        band = group_by_elevation(mid)[0]
        self.assertEqual([c['elevation_m'] for c in band['cams']], [1731, 1672, 1600, 1600, 1500])
        self.assertEqual(band['range'], '1,500 \u2013 1,731 m')
        self.assertEqual(band['cols'], 3)


class WebcamFeedTests(SimpleTestCase):
    def test_waits_for_next_capture_then_polls_conditionally(self):
        latest = T0 + 5
        clock = Clock(latest + 10)
        session = FakeSession([
            FakeResponse(200, _page(), {'ETag': 'W/"1"', 'Last-Modified': 'then'}),
            FakeResponse(304),
            FakeResponse(200, _page(T0 + 300), {'ETag': 'W/"2"'}),
        ])
        feed = WebcamFeed(session=session, clock=clock)

        cams, wait = feed.get()
        self.assertEqual(len(cams), 3)
        due = latest + feed_mod.CAPTURE_INTERVAL + feed_mod.PUBLISH_LAG
        self.assertEqual(wait, due - clock.now)

        # Before the next capture is due, nothing goes upstream.
        clock.now = due - 1
        feed.get()
        self.assertEqual(len(session.calls), 1)

        # Once due: conditional GET; a 304 keeps the data and retries soon.
        clock.now = due
        cams, wait = feed.get()
        self.assertEqual(session.calls[1][1], {'If-None-Match': 'W/"1"', 'If-Modified-Since': 'then'})
        self.assertEqual(cams[0]['timestamp'], str(T0))
        self.assertEqual(wait, feed_mod.RETRY_INTERVAL)

        clock.now += feed_mod.RETRY_INTERVAL
        cams, wait = feed.get()
        self.assertEqual(cams[0]['timestamp'], str(T0 + 300))
        self.assertEqual(wait, due + 300 - clock.now)

    def test_backs_off_when_update_is_long_overdue(self):
        clock = Clock(T0 + 5 + 2 * feed_mod.CAPTURE_INTERVAL + feed_mod.PUBLISH_LAG + 1)
        feed = WebcamFeed(session=FakeSession([FakeResponse(200, _page())]), clock=clock)
        self.assertEqual(feed.get()[1], feed_mod.LATE_INTERVAL)

    def test_errors_keep_last_good_data(self):
        clock = Clock(T0)
        session = FakeSession([requests.ConnectionError('down'), FakeResponse(200, _page()),
                               FakeResponse(500), FakeResponse(200, b'<html>maintenance</html>')])
        feed = WebcamFeed(session=session, clock=clock)
        self.assertEqual(feed.get(), ([], feed_mod.ERROR_INTERVAL))

        clock.now += feed_mod.ERROR_INTERVAL
        self.assertEqual(len(feed.get()[0]), 3)

        for _ in range(2):
            clock.now += 1000
            self.assertEqual(len(feed.get()[0]), 3)

    def test_thumbnail_is_downscaled_and_cached(self):
        buf = io.BytesIO()
        Image.new('RGB', (3840, 2160), (40, 80, 120)).save(buf, 'JPEG')
        session = FakeSession([FakeResponse(200, _page()), FakeResponse(200, buf.getvalue())])
        feed = WebcamFeed(session=session, clock=Clock(T0))

        data = feed.thumbnail('sundance', T0 + 5)
        with Image.open(io.BytesIO(data)) as im:
            self.assertEqual(im.size, (1280, 720))
        self.assertEqual(session.calls[1][0], f'{IMG_BASE}b.jpg?timestamp={T0 + 5}')

        self.assertEqual(feed.thumbnail('sundance', T0 + 5), data)
        self.assertEqual(len(session.calls), 2)
        self.assertIsNone(feed.thumbnail('nope', T0))


class WebcamEndpointTests(TestCase):
    def _feed(self, *responses):
        return mock.patch('sunpeaks_webcams.views.feed',
                          WebcamFeed(session=FakeSession([FakeResponse(200, _page()), *responses]), clock=Clock(T0)))

    def test_json_includes_poll_hint(self):
        with self._feed():
            data = self.client.get('/sunpeaks_webcams/get_webcams/').json()
        self.assertEqual([c['slug'] for c in data['webcams']], ['crystal-bowl', 'sundance', 'village-clock-tower'])
        self.assertEqual(data['poll_after'], 5 + feed_mod.CAPTURE_INTERVAL + feed_mod.PUBLISH_LAG + 3)

    def test_page_renders_elevation_bands(self):
        with self._feed():
            html = self.client.get('/sunpeaks_webcams/').content.decode()
        self.assertLess(html.index('Alpine'), html.index('Mid-mountain'))
        self.assertLess(html.index('Mid-mountain'), html.index('Village</div>'))
        self.assertIn(f'thumb/sundance/{T0 + 5}.jpg', html)

    def test_thumbnail_endpoint(self):
        buf = io.BytesIO()
        Image.new('RGB', (64, 36)).save(buf, 'JPEG')
        with self._feed(FakeResponse(200, buf.getvalue()), FakeResponse(404)):
            resp = self.client.get(f'/sunpeaks_webcams/thumb/sundance/{T0}.jpg')
            self.assertEqual(resp['Content-Type'], 'image/jpeg')
            self.assertIn('immutable', resp['Cache-Control'])

            missing = self.client.get(f'/sunpeaks_webcams/thumb/nope/{T0}.jpg')
            self.assertEqual(missing.status_code, 404)

            # Upstream failure falls back to the full-size original.
            failed = self.client.get(f'/sunpeaks_webcams/thumb/crystal-bowl/{T0}.jpg')
            self.assertRedirects(failed, f'{IMG_BASE}a.jpg?timestamp={T0}', fetch_redirect_response=False)

class CheckForNewWebcamsTests(TestCase):
    def test_check_for_new_webcams_data(self):
        # Sun Peaks adds, renames and removes (seasonal) cameras, so don't pin
        # names; just verify that every camera that is listed is well-formed
        # and fresh.
        webcams = check_for_new_webcams()
        self.assertGreater(len(webcams), 0, "No webcams found")

        image_prefix = 'https://www.sunpeaksresort.com/sites/default/files/spr_website_data/webcams/'
        pacific = pytz.timezone('America/Los_Angeles')
        now_pacific = datetime.now(pacific)

        for webcam in webcams:
            for field in ['camera_name', 'image_url', 'timestamp', 'last_updated', 'location', 'elevation']:
                self.assertIn(field, webcam, f"Expected key '{field}' missing in webcam data.")

            camera_name = webcam['camera_name']
            self.assertTrue(camera_name, "Empty camera_name")
            for field in ['location', 'elevation']:
                self.assertTrue(webcam[field], f"Empty {field} for '{camera_name}'")

            image_url = webcam['image_url']
            split_parts = image_url.split('?timestamp=')
            self.assertGreater(len(split_parts), 1, f"image_url does not contain '?timestamp=': {image_url}")
            static_url = split_parts[0]
            self.assertTrue(static_url.startswith(image_prefix),
                            f"For camera '{camera_name}', unexpected image URL '{static_url}'")
            self.assertTrue(static_url.lower().endswith('.jpg'),
                            f"For camera '{camera_name}', image URL is not a .jpg: '{static_url}'")

            ts_str = split_parts[1]
            try:
                ts_int = int(ts_str)
            except ValueError:
                self.fail(f"Timestamp '{ts_str}' in image_url for '{camera_name}' is not a valid integer")
            current_unix = int(time.time())
            self.assertLessEqual(
                current_unix - ts_int, 15*60,
                f"Timestamp {ts_int} for '{camera_name}' is older than 15 minutes."
            )

            # Remove ordinal suffixes from the day like 'th', 'st', etc.
            last_updated_text = webcam['last_updated']
            cleaned_text = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', last_updated_text)
            try:
                # The string may not include a year; assume current year, Pacific time.
                parsed_last_updated = dt_parser.parse(cleaned_text, fuzzy=True)
                parsed_last_updated = parsed_last_updated.replace(year=now_pacific.year)
            except Exception as e:
                self.fail(f"Could not parse last_updated '{last_updated_text}' for '{camera_name}': {e}")

            delta = now_pacific - pacific.localize(parsed_last_updated)
            self.assertLessEqual(delta, timedelta(minutes=15),
                f"Last updated for '{camera_name}' is more than 15 minutes old: {parsed_last_updated} (delta: {delta})")

@tag('selenium')
class SunPeaksWebcamsTest(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.browser = webdriver.Chrome(options=get_chrome_options())
        cls.browser.set_window_size(1920, 1080)

    @classmethod
    def tearDownClass(cls):
        cls.browser.quit()
        super().tearDownClass()

    def setUp(self):
        # Reset browser state between tests
        self.browser.get('about:blank')
        self.browser.delete_all_cookies()

    def test_background_color(self):
        self.browser.get(f'{self.live_server_url}/sunpeaks_webcams/')
        body = WebDriverWait(self.browser, 20).until(
            EC.presence_of_element_located((By.TAG_NAME, 'body'))
        )
        background_image = body.value_of_css_property('background-image')
        self.assertIn('radial-gradient', background_image)

    def test_images_are_displayed(self):
        self.browser.get(f'{self.live_server_url}/sunpeaks_webcams/')
        WebDriverWait(self.browser, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '#webcams img'))
        )
        images = self.browser.find_elements(By.CSS_SELECTOR, '#webcams img')
        self.assertTrue(len(images) > 0, "No images found on the page")

    def test_image_modal_opens(self):
        self.browser.get(f'{self.live_server_url}/sunpeaks_webcams/')
        first_image = WebDriverWait(self.browser, 20).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, '#webcams img'))
        )
        first_image.click()

        WebDriverWait(self.browser, 20).until(
            EC.visibility_of_element_located((By.ID, 'webcam-image-modal-image'))
        )

        modal = self.browser.find_element(By.ID, 'webcam-image-modal-image')
        self.assertTrue(modal.is_displayed(), "Modal did not open when the image was clicked")

    def test_modal_closes(self):
        self.browser.get(f'{self.live_server_url}/sunpeaks_webcams/')
        first_image = WebDriverWait(self.browser, 20).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, '#webcams img'))
        )
        first_image.click()

        WebDriverWait(self.browser, 20).until(
            EC.visibility_of_element_located((By.ID, 'webcam-image-modal-image'))
        )

        # Tap anywhere on the modal to close (tap-to-dismiss pattern)
        modal = self.browser.find_element(By.ID, 'webcam-image-modal')
        modal.click()

        WebDriverWait(self.browser, 20).until(
            EC.invisibility_of_element((By.ID, 'webcam-image-modal-image'))
        )

        modal_image = self.browser.find_element(By.ID, 'webcam-image-modal-image')
        self.assertFalse(modal_image.is_displayed(), "Modal did not close when tapped")

@tag('selenium')
class FeedbackModalTest(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.browser = webdriver.Chrome(options=get_chrome_options())
        cls.browser.set_window_size(1920, 1080)

    @classmethod
    def tearDownClass(cls):
        cls.browser.quit()
        super().tearDownClass()

    def setUp(self):
        # Reset browser state between tests
        self.browser.get('about:blank')
        self.browser.delete_all_cookies()

    def test_feedback_modal_submission(self):
        
        #get snapshot of the page
        self.browser.get(f'{self.live_server_url}/sunpeaks_webcams/')

        #wait for page to load
        WebDriverWait(self.browser, 20).until(
            EC.visibility_of_element_located((By.CLASS_NAME, "feedback-button"))
        )
        
        # Open the feedback modal
        feedback_button = self.browser.find_element(By.CLASS_NAME, "feedback-button")
        feedback_button.click()
        
        # Wait for the modal to be visible
        WebDriverWait(self.browser, 20).until(
            EC.visibility_of_element_located((By.ID, "feedback-modal"))
        )

        # Fill out the feedback form
        name_input = WebDriverWait(self.browser, 20).until(
            EC.element_to_be_clickable((By.ID, "name"))
        )
        email_input = self.browser.find_element(By.ID, "email")
        message_input = self.browser.find_element(By.ID, "message")

        name_input.send_keys("Test User")
        self.assertNotEqual(name_input.get_attribute("value"), "")
        
        email_input.send_keys("testuser@example.com")
        self.assertNotEqual(email_input.get_attribute("value"), "")
        
        message_input.send_keys("This is a test feedback message.")
        self.assertNotEqual(message_input.get_attribute("value"), "")

        # Submit the feedback form
        submit_button = self.browser.find_element(By.CSS_SELECTOR, "#feedback-form button[type='submit']")
        submit_button.click()

        # Wait for the success message to be visible
        WebDriverWait(self.browser, 20).until(
            EC.visibility_of_element_located((By.ID, "feedback-success"))
        )

        # Check that the success message is displayed
        success_message = self.browser.find_element(By.ID, "feedback-success").text
        success_message_color = self.browser.find_element(By.ID, "feedback-success").value_of_css_property('color')
        self.assertIn("Thank you for your feedback!", success_message, "Success message not displayed or incorrect")
        self.assertEqual(success_message_color, 'rgba(0, 0, 0, 1)', "Success message text color is not black")
        

        # Wait for the modal to close after a delay
        WebDriverWait(self.browser, 20).until(
            EC.invisibility_of_element((By.ID, "feedback-modal"))
        )

        # Check that the modal is closed
        modal = self.browser.find_element(By.ID, "feedback-modal")
        self.assertFalse(modal.is_displayed(), "Modal did not close after feedback submission")
        
    def test_feedback_modal_cancel(self):
                #get snapshot of the page
        self.browser.get(f'{self.live_server_url}/sunpeaks_webcams/')

        #wait for page to load
        WebDriverWait(self.browser, 20).until(
            EC.visibility_of_element_located((By.CLASS_NAME, "feedback-button"))
        )
        
        # Open the feedback modal
        feedback_button = self.browser.find_element(By.CLASS_NAME, "feedback-button")
        feedback_button.click()
        
        # Wait for the modal to be visible
        WebDriverWait(self.browser, 20).until(
            EC.visibility_of_element_located((By.ID, "feedback-modal"))
        )
        
        #hit close button
        close_button = self.browser.find_element(By.ID, "feedback-modal-close")
        close_button.click()
        
        #wait for modal to close
        WebDriverWait(self.browser, 20).until(
            EC.invisibility_of_element((By.ID, "feedback-modal"))
        )
        
        # Check that the modal is closed
        modal = self.browser.find_element(By.ID, "feedback-modal")
        self.assertFalse(modal.is_displayed(), "Modal did not close after feedback submission")
