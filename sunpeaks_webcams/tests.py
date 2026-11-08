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

from sunpeaks_webcams.views import check_for_new_webcams

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
        background_color = body.value_of_css_property('background-color')
        self.assertEqual(background_color, 'rgba(0, 0, 0, 1)')

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
