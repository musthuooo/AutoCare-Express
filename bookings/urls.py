from django.urls import path
from . import views

app_name = 'bookings'

urlpatterns = [
    path('create/<int:package_id>/', views.create_booking, name='create_booking'),
    path('packages/', views.package_list, name='packages'),
    path('my-bookings/', views.my_bookings, name='my_bookings'),
    path('supervisor-dashboard/', views.supervisor_dashboard, name='supervisor_dashboard'),
    path('update-booking-status/<int:booking_id>/', views.update_booking_status, name='update_booking_status'),
    path('cancel/<int:booking_id>/', views.cancel_booking, name='cancel_booking'),

    path('my-bookings/<int:booking_id>/archive/', views.archive_booking_user, name='archive_booking_user'),
    path('supervisor/bookings/<int:booking_id>/archive/', views.archive_booking_supervisor, name='archive_booking_supervisor'),

    path('delete/<int:booking_id>/', views.delete_booking, name='delete_booking'),
    path('manage-slots/', views.manage_time_slots, name='manage_time_slots'),
]
