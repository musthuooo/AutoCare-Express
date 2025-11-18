from django.db import models
from django.conf import settings
from django.utils import timezone
import datetime


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

    # TimeSlot link
    time_slot = models.ForeignKey(
        'TimeSlot',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='bookings'
    )

    def __str__(self):
        return f"{self.customer.username} - {self.package.name} - {self.date}"


# TimeSlot model
class TimeSlot(models.Model):
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_available = models.BooleanField(default=True)
    note = models.CharField(max_length=255, blank=True, null=True)

    capacity = models.PositiveSmallIntegerField(default=2)

    class Meta:
        ordering = ['start_time']
        verbose_name = 'Time Slot'
        verbose_name_plural = 'Time Slots'

    def __str__(self):
        try:
            return f"{self.start_time.strftime('%H:%M')} - {self.end_time.strftime('%H:%M')}"
        except Exception:
            return f"{self.start_time} - {self.end_time}"

    def bookings_count(self, date=None):
        if date is None:
            date = timezone.localdate()
        return self.bookings.filter(date=date).exclude(status='cancelled').count()

    def is_full_for_date(self, date=None):
        return self.bookings_count(date) >= (self.capacity or 0)

    def available_for_date(self, date=None):
        if not self.is_available:
            return False
        return not self.is_full_for_date(date)


# -------------------------
# UPDATED ANNOUNCEMENT MODEL
# -------------------------
TYPE_CHOICES = (
    ('important', 'Important'),
    ('warning', 'Warning'),
    ('info', 'Info'),
)

class Announcement(models.Model):
    title = models.CharField(max_length=150, null=True, blank=True)
    message = models.TextField()
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='info')
    expiry = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def _expiry_as_date(self):
        """
        Return expiry as a date() object, regardless of whether expiry is a date or datetime.
        Returns None if expiry is None.
        """
        if self.expiry is None:
            return None
        # If stored as a datetime (older data or DateTimeField), convert to date
        if isinstance(self.expiry, datetime.datetime):
            # convert timezone-aware datetimes to local date first (if necessary)
            try:
                if timezone.is_aware(self.expiry):
                    local_dt = timezone.localtime(self.expiry)
                else:
                    local_dt = self.expiry
            except Exception:
                local_dt = self.expiry
            return local_dt.date()
        # if already a date object
        if isinstance(self.expiry, datetime.date):
            return self.expiry
        # fallback: try to parse (unlikely)
        try:
            return datetime.date(self.expiry)
        except Exception:
            return None

    @property
    def is_expired(self):
        """
        True if expiry date exists and is strictly before today's local date.
        Handles expiry stored as date or datetime safely.
        """
        expiry_date = self._expiry_as_date()
        if expiry_date is None:
            return False
        today = timezone.localdate()
        return expiry_date < today

    def __str__(self):
        # Always return a string (avoid returning None)
        if self.title:
            return str(self.title)
        # fallback to a short message snippet
        if self.message:
            return str(self.message[:60])
        return f"Announcement #{self.pk or 'unsaved'}"

# Keep your existing context processor EXACTLY:
def announcement_processor(request):
    announcement = Announcement.objects.filter(is_active=True).first()
    return {'site_announcement': announcement}
