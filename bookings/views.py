from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.urls import reverse
from django.db import transaction
from django.db.models import Count
from datetime import datetime, time, timedelta

from .models import Booking, Package, TimeSlot
from users.models import CustomUser
from .forms import BookingForm


def package_list(request):
    packages = Package.objects.all()
    return render(request, 'packages.html', {'packages': packages})


@login_required
def create_booking(request, package_id):
    """
    Create booking.
    - Uses BookingForm (which shows only available TimeSlot choices for the chosen date).
    - If a TimeSlot is selected we:
        * lock the TimeSlot row (select_for_update) inside a transaction
        * re-check occupancy for the requested date
        * if capacity available: set booking.time = slot.start_time and booking.time_slot = slot and save
        * update slot.is_available if it reached capacity
    """
    package = get_object_or_404(Package, id=package_id)

    user_profile = getattr(request.user, 'profile', None)
    default_address = user_profile.address if user_profile else ''
    default_contact = user_profile.contact_number if user_profile else ''

    if request.method == 'POST':
        form = BookingForm(request.POST)
        if form.is_valid():
            # prepare an unsaved booking instance
            booking = form.save(commit=False)
            booking.customer = request.user
            booking.package = package
            booking.address = default_address
            booking.contact_number = default_contact

            # require a timeslot (BookingForm should already enforce this)
            slot = form.cleaned_data.get('time_slot')

            # If a managed slot is selected, we must check capacity atomically
            if slot:
                try:
                    with transaction.atomic():
                        # Lock the timeslot row to reduce race conditions
                        slot_locked = TimeSlot.objects.select_for_update().get(pk=slot.pk)

                        # Count current non-cancelled bookings for the slot on the chosen date
                        occupied = (Booking.objects
                                    .filter(time_slot=slot_locked, date=booking.date)
                                    .exclude(status='cancelled')
                                    .count())

                        # Determine capacity (fallback to 2 if not set or invalid)
                        try:
                            capacity = int(slot_locked.capacity) if slot_locked.capacity is not None else 2
                        except Exception:
                            capacity = 2

                        if occupied >= capacity:
                            # Slot is full — add error and re-render form
                            form.add_error('time_slot', 'Selected slot is fully booked. Please choose another slot or date.')
                            messages.error(request, "Please correct the errors below.")
                            context = {
                                'form': form,
                                'package': package,
                                'default_address': default_address,
                                'default_contact': default_contact,
                            }
                            return render(request, 'create_booking.html', context)

                        # Reserve: set booking time & slot and save booking
                        booking.time = slot_locked.start_time
                        booking.time_slot = slot_locked
                        booking.save()

                        # Recompute occupancy (we just created one)
                        occupied += 1

                        # If capacity reached, mark unavailable
                        if occupied >= capacity and slot_locked.is_available:
                            slot_locked.is_available = False
                            slot_locked.save(update_fields=['is_available'])

                        # Success
                        messages.success(request, "Your booking has been submitted! We will contact you soon.")
                        return redirect('bookings:my_bookings')
                except TimeSlot.DoesNotExist:
                    form.add_error('time_slot', 'Selected time slot no longer exists.')
                    messages.error(request, "Please correct the errors below.")
            else:
                # No slot selected — BookingForm.clean should catch this, but guard anyway
                form.add_error('time_slot', 'Please select an available time slot.')
                messages.error(request, "Please correct the errors below.")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        # GET - show empty form; BookingForm will filter timeslots for today's date if supplied via initial
        form = BookingForm(initial={})

    context = {
        'form': form,
        'package': package,
        'default_address': default_address,
        'default_contact': default_contact,
    }
    return render(request, 'create_booking.html', context)


@login_required
def my_bookings(request):
    if getattr(request.user, "role", "") != 'customer':
        messages.error(request, "Access denied: only customers can view this page.")
        return redirect('users:dashboard')

    bookings = (Booking.objects
                .filter(customer=request.user, is_archived_customer=False)
                .order_by('-date', '-created_at'))

    archived_bookings = (Booking.objects
                         .filter(customer=request.user, is_archived_customer=True)
                         .order_by('-date', '-created_at'))

    return render(request, 'my_bookings.html', {
        'bookings': bookings,
        'archived_bookings': archived_bookings,
    })


@login_required
def supervisor_dashboard(request):
    # Guard: only supervisors
    if getattr(request.user, "role", "") != "supervisor":
        messages.error(request, "Access denied: supervisors only.")
        return redirect('users:dashboard')

    bookings = Booking.objects.filter(is_archived_supervisor=False).order_by('-date', '-created_at')
    archived_bookings = Booking.objects.filter(is_archived_supervisor=True).order_by('-date', '-created_at')

    total = bookings.count()
    pending = bookings.filter(status='pending').count()
    in_progress = bookings.filter(status='in_progress').count()
    completed = bookings.filter(status='completed').count()

    timeslots = TimeSlot.objects.all().order_by('start_time')

    context = {
        'bookings': bookings,
        'archived_bookings': archived_bookings,
        'total': total,
        'pending': pending,
        'in_progress': in_progress,
        'completed': completed,
        'timeslots': timeslots,
    }
    return render(request, 'supervisor_dashboard.html', context)


@login_required
def update_booking_status(request, booking_id):
    """
    Supervisor changes booking status. After changing, re-evaluate slot availability.
    """
    booking = get_object_or_404(Booking, id=booking_id)

    if request.method == 'POST':
        new_status = request.POST.get('status')
        booking.status = new_status
        booking.save()

        # Re-evaluate slot availability for the booking.date
        slot = getattr(booking, 'time_slot', None)
        if slot:
            occupied = slot.bookings.filter(date=booking.date).exclude(status='cancelled').count()
            try:
                capacity = int(slot.capacity) if slot.capacity is not None else 2
            except Exception:
                capacity = 2

            if occupied >= capacity:
                if slot.is_available:
                    slot.is_available = False
                    slot.save(update_fields=['is_available'])
            else:
                # Optionally re-enable slot when occupancy < capacity
                if not slot.is_available:
                    slot.is_available = True
                    slot.save(update_fields=['is_available'])

        messages.success(request, f"Booking status updated to {booking.get_status_display()} ")
        return redirect('bookings:supervisor_dashboard')

    status_choices = Booking.STATUS_CHOICES
    return render(request, 'update_booking.html', {
        'booking': booking,
        'status_choices': status_choices,
    })


@login_required
def cancel_booking(request, booking_id):
    """
    Customer cancels a booking (only pending allowed).
    After cancellation, re-evaluate the slot availability.
    """
    booking = get_object_or_404(Booking, id=booking_id, customer=request.user)

    if booking.status == 'pending':
        slot = getattr(booking, 'time_slot', None)

        booking.status = 'cancelled'
        booking.save()

        # If slot exists, re-evaluate occupancy and possibly re-enable the slot
        if slot:
            occupied = slot.bookings.filter(date=booking.date).exclude(status='cancelled').count()
            try:
                capacity = int(slot.capacity) if slot.capacity is not None else 2
            except Exception:
                capacity = 2

            if occupied < capacity:
                if not slot.is_available:
                    slot.is_available = True
                    slot.save(update_fields=['is_available'])

        messages.success(request, "Your booking has been cancelled successfully.")
    else:
        messages.warning(request, "Only pending bookings can be cancelled.")

    return redirect('bookings:my_bookings')


@login_required
def archive_booking_user(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id, customer=request.user)

    if booking.status in ['completed', 'cancelled']:
        if not booking.is_archived_customer:
            booking.is_archived_customer = True
            booking.customer_archived_at = timezone.now()
            booking.customer_archived_by = request.user
            booking.save(update_fields=[
                'is_archived_customer',
                'customer_archived_at',
                'customer_archived_by'
            ])
    else:
        messages.error(request, "Only completed or cancelled bookings can be archived.")
    return redirect('bookings:my_bookings')


@login_required
def archive_booking_supervisor(request, booking_id):
    if getattr(request.user, "role", "") != "supervisor":
        messages.error(request, "Access denied: supervisors only.")
        return redirect('users:dashboard')

    booking = get_object_or_404(Booking, id=booking_id)

    if booking.status in ['completed', 'cancelled']:
        if not booking.is_archived_supervisor:
            booking.is_archived_supervisor = True
            booking.supervisor_archived_at = timezone.now()
            booking.supervisor_archived_by = request.user
            booking.save(update_fields=[
                'is_archived_supervisor',
                'supervisor_archived_at',
                'supervisor_archived_by'
            ])
    else:
        messages.error(request, "Only completed or cancelled bookings can be archived.")
    return redirect('bookings:supervisor_dashboard')


def delete_booking(request, booking_id):
    """
    Legacy route — treat as supervisor archive ONLY (no hard delete)
    """
    booking = get_object_or_404(Booking, id=booking_id)

    if booking.status in ['completed', 'cancelled']:
        if not booking.is_archived_supervisor:
            booking.is_archived_supervisor = True
            booking.supervisor_archived_at = timezone.now()
            if request.user.is_authenticated:
                booking.supervisor_archived_by = request.user
            booking.save(update_fields=[
                'is_archived_supervisor',
                'supervisor_archived_at',
                'supervisor_archived_by'
            ])
    else:
        messages.error(request, "Only completed or cancelled bookings can be archived.")
    return redirect('bookings:supervisor_dashboard')


@login_required
def manage_time_slots(request):
    """
    Supervisor view to manage time slots.
    - Supports generate default schedule, add, toggle availability, delete and update capacity.
    - Shows `slot.booked_count` for the selected date to the template.
    """
    if getattr(request.user, "role", "") != "supervisor":
        messages.error(request, "Access denied: supervisors only.")
        return redirect('users:dashboard')

    # Determine view_date (GET param), default to today
    view_date_str = request.GET.get('view_date')
    try:
        if view_date_str:
            view_date = datetime.strptime(view_date_str, "%Y-%m-%d").date()
        else:
            view_date = timezone.localdate()
    except Exception:
        view_date = timezone.localdate()

    # Fetch timeslots and compute booked counts for view_date
    timeslot_qs = TimeSlot.objects.all().order_by('start_time')
    bookings_per_slot_qs = (
        Booking.objects
        .filter(time_slot__isnull=False, date=view_date)
        .values('time_slot')
        .annotate(cnt=Count('id'))
    )
    bookings_per_slot = {item['time_slot']: item['cnt'] for item in bookings_per_slot_qs}

    timeslots = []
    for slot in timeslot_qs:
        slot.booked_count = bookings_per_slot.get(slot.id, 0)
        timeslots.append(slot)

    if request.method == 'POST':
        post_view_date = request.POST.get('view_date') or view_date_str

        # Generate default slots (09:00 -> 18:00, 30-min, skip 13:00-14:00)
        if 'generate_slots' in request.POST:
            start_dt = datetime.combine(datetime.today(), time(hour=9, minute=0))
            end_dt = datetime.combine(datetime.today(), time(hour=18, minute=0))
            slot_length = timedelta(minutes=30)

            created = 0
            current = start_dt
            while current < end_dt:
                slot_start = current.time()
                slot_end = (current + slot_length).time()

                # Skip break window 13:00-14:00
                if slot_start >= time(hour=13, minute=0) and slot_start < time(hour=14, minute=0):
                    current += slot_length
                    continue

                if not TimeSlot.objects.filter(start_time=slot_start, end_time=slot_end).exists():
                    TimeSlot.objects.create(start_time=slot_start, end_time=slot_end, is_available=True, capacity=2)
                    created += 1

                current += slot_length

            if created:
                messages.success(request, f"Generated {created} time slot(s).")
            else:
                messages.info(request, "No new slots were generated (they may already exist).")

            redirect_url = reverse('bookings:manage_time_slots')
            if post_view_date:
                redirect_url += f'?view_date={post_view_date}'
            return redirect(redirect_url)

        # Add a new slot
        if 'add_slot' in request.POST:
            start = request.POST.get('start_time')
            end = request.POST.get('end_time')
            note = request.POST.get('note', '').strip() or None

            if not start or not end:
                messages.error(request, "Please provide both start and end times for the slot.")
            else:
                try:
                    start_parsed = datetime.strptime(start, "%H:%M").time()
                    end_parsed = datetime.strptime(end, "%H:%M").time()
                except ValueError:
                    messages.error(request, "Invalid time format. Use HH:MM.")
                    redirect_url = reverse('bookings:manage_time_slots')
                    if post_view_date:
                        redirect_url += f'?view_date={post_view_date}'
                    return redirect(redirect_url)

                if TimeSlot.objects.filter(start_time=start_parsed, end_time=end_parsed).exists():
                    messages.warning(request, "A slot with the same start and end times already exists.")
                else:
                    TimeSlot.objects.create(start_time=start_parsed, end_time=end_parsed, note=note, capacity=2)
                    messages.success(request, "Time slot added successfully.")

            redirect_url = reverse('bookings:manage_time_slots')
            if post_view_date:
                redirect_url += f'?view_date={post_view_date}'
            return redirect(redirect_url)

        # Toggle availability
        if 'toggle' in request.POST:
            slot_id = request.POST.get('slot_id')
            slot = get_object_or_404(TimeSlot, id=slot_id)
            slot.is_available = not slot.is_available
            slot.save(update_fields=['is_available'])
            messages.success(request, "Slot availability toggled.")
            redirect_url = reverse('bookings:manage_time_slots')
            if post_view_date:
                redirect_url += f'?view_date={post_view_date}'
            return redirect(redirect_url)

        # Delete slot (only if no bookings for the selected date)
        if 'delete' in request.POST:
            slot_id = request.POST.get('slot_id')
            slot = get_object_or_404(TimeSlot, id=slot_id)
            booked_count = Booking.objects.filter(time_slot=slot, date=view_date).count()
            if booked_count > 0:
                messages.error(request, "Cannot delete a slot that has bookings for the selected date.")
            else:
                slot.delete()
                messages.success(request, "Slot deleted.")
            redirect_url = reverse('bookings:manage_time_slots')
            if post_view_date:
                redirect_url += f'?view_date={post_view_date}'
            return redirect(redirect_url)

        # Update capacity
        if 'update_capacity' in request.POST:
            slot_id = request.POST.get('slot_id')
            new_capacity = request.POST.get('capacity')
            try:
                new_capacity_int = int(new_capacity)
                if new_capacity_int < 1:
                    raise ValueError
            except Exception:
                messages.error(request, "Capacity must be a positive integer.")
                redirect_url = reverse('bookings:manage_time_slots')
                if post_view_date:
                    redirect_url += f'?view_date={post_view_date}'
                return redirect(redirect_url)

            slot = get_object_or_404(TimeSlot, id=slot_id)
            slot.capacity = new_capacity_int
            slot.save(update_fields=['capacity'])
            messages.success(request, "Slot capacity updated.")

            occupied = Booking.objects.filter(time_slot=slot, date=view_date).exclude(status='cancelled').count()
            if occupied >= slot.capacity and slot.is_available:
                slot.is_available = False
                slot.save(update_fields=['is_available'])

            redirect_url = reverse('bookings:manage_time_slots')
            if post_view_date:
                redirect_url += f'?view_date={post_view_date}'
            return redirect(redirect_url)

    # GET: render template
    return render(request, 'manage_slots.html', {
        'timeslots': timeslots,
        'view_date': view_date,
    })
