from django.urls import path
from . import views

urlpatterns = [
    path('', views.webcams, name='webcams'),
    path('get_webcams/', views.check_for_new_webcams_json, name='check_for_new_webcams_json'),
]