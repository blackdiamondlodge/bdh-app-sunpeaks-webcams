from django.urls import path
from . import views

urlpatterns = [
    path('', views.webcams, name='webcams'),
    path('get_webcams/', views.check_for_new_webcams_json, name='check_for_new_webcams_json'),
    path('latest/<slug:slug>.jpg', views.webcam_latest, name='webcam_latest'),
    path('thumb/<slug:slug>/<int:timestamp>.jpg', views.webcam_thumbnail, name='webcam_thumbnail'),
]