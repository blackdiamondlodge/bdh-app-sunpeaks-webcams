from django.shortcuts import render
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlsplit, parse_qs
from django.http import JsonResponse

# Create your views here.
def webcams(request):

    # Check for new webcams
    webcams = check_for_new_webcams()
    
    context = {
        'sunpeaks_webcams': webcams
    }
    
    return render(request, 'sunpeaks_webcams/webcams.html', context)

def check_for_new_webcams_json(request):

    webcams_list = check_for_new_webcams()  # Get the webcam data
    return JsonResponse(webcams_list, safe=False)

def check_for_new_webcams():

    url = 'https://www.sunpeaksresort.com/bike-hike/weather-webcams/webcams'  # Replace with the URL you want to fetch
    domain = 'https://www.sunpeaksresort.com'
    response = requests.get(url)
    
    # Parse the HTML content
    soup = BeautifulSoup(response.content, 'html.parser')

    # Extract specific content
    content = soup.find('div', {'id': 'webcams'})
    
    if content:
        cameras = soup.find_all('div', class_='cam')
        webcams_list = []
              
        for cam in cameras:
            camera_name = cam.find('h2').text.strip()

            # Extract image URL from the <img> tag
            image_url = cam.find('div', class_='image').find('img')['src']
            if image_url.startswith('/'):
                image_url = domain + image_url
                
            if 'timestamp=' in image_url:
              split_url = urlsplit(image_url)
              query_params = parse_qs(split_url.query)
              timestamp = query_params.get("timestamp", [None])[0]

            # Extract table rows to get last_updated, location, and elevation
            table_rows = cam.find('table').find_all('tr')
            last_updated = table_rows[0].find_all('td')[1].text.strip()
            location = table_rows[1].find_all('td')[1].text.strip()
            elevation = table_rows[2].find_all('td')[1].text.strip()

            # Create a structured dictionary for each camera
            webcam_info = {
                'camera_name': camera_name,
                'image_url': image_url,
                'timestamp': timestamp,
                'last_updated': last_updated,
                'location': location,
                'elevation': elevation
            }
            
            # Append to the list of camera data
            webcams_list.append(webcam_info)              
                
    return webcams_list
    
    