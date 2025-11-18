from django import forms
from django.utils import timezone
from datetime import datetime

from .models import Booking, TimeSlot


class BookingForm(forms.ModelForm):
    """
    Booking form that:
    - Removes manual `time` input (we use managed `time_slot` instead).
    - Shows only available TimeSlot instances (is_available=True) and
      hides slots that are already full for the selected date.
    - Requires 'custom_vehicle_type' when vehicle_type == 'other'.
    - Validates slot capacity defensively (uses slot.capacity if present; default=2).
    """

    custom_vehicle_type = forms.CharField(
        max_length=50,
        required=False,
        label="Specify vehicle",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Type your vehicle'
        })
    )

    class Meta:
        model = Booking
        # NOTE: `time` removed intentionally — time will be taken from selected time_slot
        fields = ['vehicle_type', 'custom_vehicle_type', 'date', 'time_slot']
        widgets = {
            'vehicle_type': forms.Select(attrs={'class': 'form-select'}),
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'time_slot': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        """
        Build the initial queryset for time_slot:
          - Only TimeSlot objects where is_available=True
          - If a date is present (in bound data or initial), remove slots that are full for that date.
        """
        super().__init__(*args, **kwargs)

        # Base queryset: slots supervisors marked available
        qs = TimeSlot.objects.filter(is_available=True).order_by('start_time')

        # Determine date from bound form data (preferred) or initial data
        date_val = None

        # 1) If the form is bound, self.data contains values with widget prefixes.
        if self.is_bound and self.data:
            # attempt both prefixed and plain names
            # self.add_prefix('date') handles prefixing if form is used as a subform
            date_keys = [self.add_prefix('date'), 'date']
            for key in date_keys:
                date_str = self.data.get(key)
                if date_str:
                    try:
                        # expect YYYY-MM-DD from date input
                        date_val = datetime.strptime(date_str, "%Y-%m-%d").date()
                        break
                    except Exception:
                        date_val = None

        # 2) fallback to initial (could be date object or string)
        if date_val is None:
            initial_date = self.initial.get('date')
            if initial_date:
                if isinstance(initial_date, str):
                    try:
                        date_val = datetime.strptime(initial_date, "%Y-%m-%d").date()
                    except Exception:
                        date_val = None
                else:
                    date_val = initial_date

        # 3) Optionally: prevent past date selection server-side (we don't block here,
        #    but you can choose to add validation elsewhere)
        # If date_val is provided, filter out slots that are already full for that date.
        if date_val:
            available_ids = []
            for slot in qs:
                # compute occupied bookings on that date (exclude cancelled)
                occupied = Booking.objects.filter(time_slot=slot, date=date_val).exclude(status='cancelled').count()

                # capacity fallback: use slot.capacity if present, otherwise default to 2
                capacity = getattr(slot, 'capacity', None)
                try:
                    capacity_num = int(capacity) if capacity is not None else 2
                except Exception:
                    capacity_num = 2

                if occupied < capacity_num:
                    available_ids.append(slot.id)

            qs = qs.filter(id__in=available_ids)

        # assign queryset and label
        self.fields['time_slot'].queryset = qs
        self.fields['time_slot'].label = "Available Time Slots"

    def clean(self):
        """
        Validate:
          - custom_vehicle_type required when vehicle_type == 'other'
          - date must be present and not in the past (optional but strongly recommended)
          - slot capacity rechecked defensively for chosen date
        """
        cleaned_data = super().clean()

        vehicle_type = cleaned_data.get('vehicle_type')
        custom = (cleaned_data.get('custom_vehicle_type') or "").strip()
        date_val = cleaned_data.get('date')
        slot = cleaned_data.get('time_slot')

        # vehicle type check
        if vehicle_type == 'other' and not custom:
            self.add_error('custom_vehicle_type', "Please specify your vehicle when selecting 'Other'.")

        # date presence
        if not date_val:
            self.add_error('date', "Please choose a date for the booking.")
        else:
            # optional: disallow past dates
            today = timezone.localdate()
            if date_val < today:
                self.add_error('date', "Selected date is in the past. Please choose a future date.")

        # slot capacity: if a slot is chosen, double-check it still has capacity
        if slot and date_val:
            occupied = Booking.objects.filter(time_slot=slot, date=date_val).exclude(status='cancelled').count()
            capacity = getattr(slot, 'capacity', None)
            try:
                capacity_num = int(capacity) if capacity is not None else 2
            except Exception:
                capacity_num = 2

            if occupied >= capacity_num:
                self.add_error('time_slot', f"Selected time slot is fully booked ({capacity_num} vehicles). Please choose another slot or date.")

        # if slot chosen but no date, ensure user picks date (date error above will cover)
        if slot and not date_val:
            self.add_error('date', "Please choose a date to confirm the selected time slot.")

        return cleaned_data
