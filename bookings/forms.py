# bookings/forms.py
from django import forms
from django.utils import timezone
from datetime import datetime
from .models import Booking, TimeSlot, Announcement, TYPE_CHOICES

# ---------------------------
# BookingForm (kept & polished)
# ---------------------------
class BookingForm(forms.ModelForm):
    """
    Robust BookingForm:
    - Uses managed time_slot (no separate manual `time` input).
    - When bound, parses date from POST in multiple formats and filters available slots for that date.
    - If a time_slot id is present in POST, ensures it is included in the queryset so ModelChoiceField accepts it.
    - Defensive capacity handling (slot.capacity or default=2).
    """

    custom_vehicle_type = forms.CharField(
        max_length=50,
        required=False,
        label="Specify vehicle",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Type your vehicle'})
    )

    class Meta:
        model = Booking
        fields = ['vehicle_type', 'custom_vehicle_type', 'date', 'time_slot']
        widgets = {
            'vehicle_type': forms.Select(attrs={'class': 'form-select'}),
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'time_slot': forms.Select(attrs={'class': 'form-select'}),
        }

    def _parse_date_from_str(self, date_str):
        """Try several common formats, return date object or None."""
        if not date_str:
            return None
        s = date_str.strip()
        # ISO-like YYYY-MM-DD quick check
        try:
            if len(s) >= 10 and s[4] == '-' and s[7] == '-':
                return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except Exception:
            pass

        for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except Exception:
                continue

        # fallback: try iso parse
        try:
            parsed = datetime.fromisoformat(s)
            return parsed.date()
        except Exception:
            try:
                parsed = datetime.strptime(s[:10], "%Y-%m-%d")
                return parsed.date()
            except Exception:
                return None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Base queryset: only globally available slots (supervisor toggled)
        base_qs = TimeSlot.objects.filter(is_available=True).order_by('start_time')

        # Determine date provided by user (prefer bound data)
        date_val = None

        if self.is_bound:
            # keys to attempt: add_prefix('date') and plain 'date'
            keys = [self.add_prefix('date'), 'date']
            for k in keys:
                if k in self.data:
                    date_val = self._parse_date_from_str(self.data.get(k))
                    if date_val:
                        break

        # fallback to initial if present
        if date_val is None:
            initial_date = self.initial.get('date')
            if initial_date:
                if isinstance(initial_date, str):
                    date_val = self._parse_date_from_str(initial_date)
                else:
                    date_val = initial_date

        # If we have a date, compute available slot ids for that date (based on capacity)
        if date_val:
            available_ids = []
            for slot in base_qs:
                occupied = Booking.objects.filter(time_slot=slot, date=date_val).exclude(status='cancelled').count()
                try:
                    capacity_num = int(slot.capacity) if slot.capacity is not None else 2
                except Exception:
                    capacity_num = 2
                if occupied < capacity_num:
                    available_ids.append(slot.id)
            qs = base_qs.filter(id__in=available_ids)
        else:
            # if date not provided, show all globally available slots
            qs = base_qs

        # Ensure submitted slot remains valid on POST (include it in queryset)
        if self.is_bound:
            time_slot_keys = [self.add_prefix('time_slot'), 'time_slot']
            submitted_slot_id = None
            for k in time_slot_keys:
                if k in self.data and self.data.get(k):
                    submitted_slot_id = self.data.get(k)
                    break
            if submitted_slot_id:
                try:
                    submitted_pk = int(submitted_slot_id)
                    extra = TimeSlot.objects.filter(pk=submitted_pk)
                except Exception:
                    extra = TimeSlot.objects.filter(pk=submitted_slot_id)
                if extra.exists():
                    qs = (qs | extra).distinct().order_by('start_time')

        # set final queryset and label
        self.fields['time_slot'].queryset = qs
        self.fields['time_slot'].label = "Available Time Slots"

    def clean(self):
        cleaned = super().clean()

        vehicle_type = cleaned.get('vehicle_type')
        custom = (cleaned.get('custom_vehicle_type') or "").strip()
        date_val = cleaned.get('date')
        slot = cleaned.get('time_slot')

        # require custom vehicle when vehicle_type == 'other'
        if vehicle_type == 'other' and not custom:
            self.add_error('custom_vehicle_type', "Please specify your vehicle when selecting 'Other'.")

        # date presence and past-date check
        if not date_val:
            self.add_error('date', "Please choose a date for the booking.")
        else:
            today = timezone.localdate()
            if date_val < today:
                self.add_error('date', "Selected date is in the past. Please choose a future date.")

        # re-check slot capacity server-side
        if slot and date_val:
            occupied = Booking.objects.filter(time_slot=slot, date=date_val).exclude(status='cancelled').count()
            try:
                capacity_num = int(slot.capacity) if slot.capacity is not None else 2
            except Exception:
                capacity_num = 2
            if occupied >= capacity_num:
                self.add_error('time_slot',
                               f"Selected time slot is fully booked ({capacity_num} vehicles). Please choose another slot or date.")

        # if slot chosen but no date, force date error (should rarely happen)
        if slot and not date_val:
            self.add_error('date', "Please choose a date to confirm the selected time slot.")

        return cleaned


# ---------------------------
# AnnouncementForm (NEW/UPDATED)
# ---------------------------
class AnnouncementForm(forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ['title', 'message', 'type', 'expiry', 'is_active']
        widgets = {
            'title':   forms.TextInput(attrs={'class':'form-control', 'placeholder':'e.g. Shop Closed Tomorrow', 'maxlength':'150', 'id':'id_title'}),
            'message': forms.Textarea(attrs={'class':'form-control', 'rows':4, 'placeholder':'Type your announcement...', 'id':'id_message'}),
            'type': forms.Select(attrs={'class':'form-select','id':'id_type'}, choices=TYPE_CHOICES),
            'expiry':  forms.DateInput(attrs={'class':'form-control', 'type':'date', 'id':'id_expiry'}),
            'is_active': forms.CheckboxInput(attrs={'class':'form-check-input', 'id':'id_is_active'}),
        }

    def clean_expiry(self):
        expiry = self.cleaned_data.get('expiry')
        if expiry and expiry < timezone.localdate():
            raise forms.ValidationError("Expiry must be today or a future date.")
        return expiry

    def clean(self):
        cleaned = super().clean()
        # Ensure message exists (extra safety)
        msg = (cleaned.get('message') or "").strip()
        if not msg:
            self.add_error('message', "Message cannot be empty.")
        return cleaned
