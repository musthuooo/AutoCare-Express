from django.db import models
from django.conf import settings
from django.utils import timezone


# Create your models here.

class Package(models.Model):
    name = models.CharField(max_length=50)
    description = models.TextField()
    price = models.DecimalField(max_digits=7, decimal_places=2)

    def __str__(self):
        return self.name


VEHICLE_CHOICES = (
    ('bike', 'Bike'),
    ('car', 'Car'),
    ('suv', 'SUV'),
    ('other', 'Other'),
)


class Booking(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    )

    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    vehicle_type = models.CharField(max_length=10, choices=VEHICLE_CHOICES)
    package = models.ForeignKey(Package, on_delete=models.CASCADE)
    address = models.TextField(blank=True, null=True)
    date = models.DateField()
    contact_number = models.CharField(max_length=15, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    custom_vehicle_type = models.CharField(max_length=50, blank=True, null=True)


    # --- NEW: archive controls kept separate for each side ---

    # Customer-side archive
    is_archived_customer = models.BooleanField(default=False)
    customer_archived_at = models.DateTimeField(blank=True, null=True)
    customer_archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='customer_archived_bookings',
    )

    # Supervisor-side archive
    is_archived_supervisor = models.BooleanField(default=False)
    supervisor_archived_at = models.DateTimeField(blank=True, null=True)
    supervisor_archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='supervisor_archived_bookings',
    )

    # --- NEW: link to TimeSlot (optional) ---
    # This is nullable so existing bookings keep the current `time` value.
    # When a slot is selected it points to the managed TimeSlot instance.
    time_slot = models.ForeignKey(
        'TimeSlot',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='bookings'
    )

    def __str__(self):
        return f"{self.customer.username} - {self.package.name} - {self.date}"


# --- NEW: TimeSlot model for supervisors to manage available slots ---
class TimeSlot(models.Model):
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_available = models.BooleanField(default=True)
    note = models.CharField(max_length=255, blank=True, null=True)

    # NEW: how many bookings allowed per slot (per date). Default 2 as requested.
    capacity = models.PositiveSmallIntegerField(default=2)

    class Meta:
        ordering = ['start_time']
        verbose_name = 'Time Slot'
        verbose_name_plural = 'Time Slots'

    def __str__(self):
        # formatted human readable representation
        try:
            return f"{self.start_time.strftime('%H:%M')} - {self.end_time.strftime('%H:%M')}"
        except Exception:
            return f"{self.start_time} - {self.end_time}"

    def bookings_count(self, date=None):
        """
        Return number of bookings that reference this TimeSlot for the given date.
        Excludes cancelled bookings (so cancelled frees up capacity).
        If date is None, defaults to today (local date).
        """
        if date is None:
            date = timezone.localdate()
        return self.bookings.filter(date=date).exclude(status='cancelled').count()

    def is_full_for_date(self, date=None):
        """
        Return True if bookings_count(date) >= capacity.
        """
        return self.bookings_count(date) >= (self.capacity or 0)

    def available_for_date(self, date=None):
        """
        Convenience: True if the slot is marked available and not full for the date.
        """
        if not self.is_available:
            return False
        return not self.is_full_for_date(date)
