# bookings/views.py

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.utils import timezone
from django.urls import reverse
from django.db import transaction
from django.db.models import Count
from django.http import JsonResponse

from datetime import datetime, time, timedelta

from .models import Booking, Package, TimeSlot, Announcement
from users.models import CustomUser
from .forms import BookingForm, AnnouncementForm


def package_list(request):
    packages = Package.objects.all()
    return render(request, 'packages.html', {'packages': packages})


# Utility
def _default_capacity(slot):
    try:
        return int(slot.capacity) if slot.capacity is not None else 2
    except Exception:
        return 2


# -----------------------------------------------------------
#  A J A X   S L O T   A P I
# -----------------------------------------------------------

@login_required
def get_slots_for_date(request):
    """
    Returns time slot availability for a selected date.
    Accepts flexible date formats and returns a JSON list of slots.
    """
    date_str = request.GET.get("date", "").strip()
    if not date_str:
        return JsonResponse({"slots": []})

    # Try several common date formats so the endpoint is robust.
    parsed_date = None
    formats_to_try = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"]
    for fmt in formats_to_try:
        try:
            parsed_date = datetime.strptime(date_str, fmt).date()
            break
        except Exception:
            continue

    if parsed_date is None:
        # final fallback: try ISO parse
        try:
            parsed_date = datetime.fromisoformat(date_str).date()
        except Exception:
            return JsonResponse({"slots": []})

    slots = TimeSlot.objects.all().order_by("start_time")

    result = []
    for slot in slots:
        # Count only non-cancelled bookings for that slot/date
        booked = Booking.objects.filter(
            time_slot=slot,
            date=parsed_date
        ).exclude(status="cancelled").count()

        try:
            capacity = int(slot.capacity) if slot.capacity is not None else 2
        except Exception:
            capacity = 2

        available = booked < capacity

        result.append({
            "id": slot.id,
            "start": slot.start_time.strftime("%I:%M %p"),
            "end": slot.end_time.strftime("%I:%M %p"),
            "available": available,
            "booked_count": booked,
            "capacity": capacity,
        })

    return JsonResponse({"slots": result})

# -----------------------------------------------------------
#  C R E A T E   B O O K I N G (updated for AJAX slots)
# -----------------------------------------------------------
@login_required
def create_booking(request, package_id):
    """
    Create a booking with proper transactional reservation
    for managed TimeSlot capacity.
    """
    package = get_object_or_404(Package, id=package_id)

    # Autofill address/contact from user profile
    user_profile = getattr(request.user, "profile", None)
    default_address = getattr(user_profile, "address", "") if user_profile else ""
    default_contact = getattr(user_profile, "contact_number", "") if user_profile else ""

    if request.method == "POST":
        form = BookingForm(request.POST)
        if form.is_valid():
            booking = form.save(commit=False)
            booking.customer = request.user
            booking.package = package
            booking.address = default_address
            booking.contact_number = default_contact

            slot = form.cleaned_data.get("time_slot")

            if slot:
                try:
                    with transaction.atomic():
                        slot_locked = TimeSlot.objects.select_for_update().get(pk=slot.pk)

                        occupied = (
                            Booking.objects
                            .filter(time_slot=slot_locked, date=booking.date)
                            .exclude(status="cancelled")
                            .count()
                        )

                        capacity = _default_capacity(slot_locked)

                        if occupied >= capacity:
                            form.add_error(
                                "time_slot",
                                "Selected slot is fully booked. Choose another slot."
                            )
                            messages.error(request, "Please correct the errors below.")
                            return render(request, "create_booking.html", {
                                "form": form,
                                "package": package,
                                "default_address": default_address,
                                "default_contact": default_contact,
                            })

                        # Reserve the slot
                        booking.time = slot_locked.start_time
                        booking.time_slot = slot_locked
                        booking.save()

                        occupied += 1
                        if occupied >= capacity and slot_locked.is_available:
                            slot_locked.is_available = False
                            slot_locked.save(update_fields=["is_available"])

                        messages.success(request, "Your booking has been submitted!")
                        return redirect("bookings:my_bookings")

                except TimeSlot.DoesNotExist:
                    form.add_error("time_slot", "Selected slot no longer exists.")
                    messages.error(request, "Please correct the errors below.")

            else:
                form.add_error("time_slot", "Please select a time slot.")
                messages.error(request, "Please correct the errors below.")
        else:
            messages.error(request, "Please correct the errors below.")

    else:
        form = BookingForm()

    return render(request, "create_booking.html", {
        "form": form,
        "package": package,
        "default_address": default_address,
        "default_contact": default_contact,
    })


# -----------------------------------------------------------
#   M Y   B O O K I N G S
# -----------------------------------------------------------
@login_required
def my_bookings(request):
    if getattr(request.user, "role", "") != "customer":
        messages.error(request, "Only customers can view this page.")
        return redirect("users:dashboard")

    bookings = Booking.objects.filter(
        customer=request.user,
        is_archived_customer=False
    ).order_by("-date", "-created_at")

    archived_bookings = Booking.objects.filter(
        customer=request.user,
        is_archived_customer=True
    ).order_by("-date", "-created_at")

    today = timezone.localdate()

    if hasattr(Announcement, "expiry"):
        announcement = Announcement.objects.filter(
        is_active=True,
        expiry__gte=today
        ).order_by("-created_at").first()
    else:
        announcement = Announcement.objects.filter(is_active=True).order_by("-created_at").first()




    return render(request, "my_bookings.html", {
        "bookings": bookings,
        "archived_bookings": archived_bookings,
        "announcement": announcement,
    })

def is_supervisor(user):
    """
    Permission check used by @user_passes_test.
    Returns True for users with role == 'supervisor' or Django staff users.
    Place this function BEFORE any view that uses it as a decorator.
    """
    return getattr(user, "role", "") == "supervisor" or getattr(user, "is_staff", False)



@login_required
@user_passes_test(is_supervisor)
def supervisor_dashboard(request):
    """
    Supervisor dashboard: aggregates bookings and shows timeslots
    availability for a selected date (via GET ?view_date=YYYY-MM-DD).
    """
    # parse requested view_date (default: today)
    view_date_str = request.GET.get("view_date")
    try:
        if view_date_str:
            view_date = datetime.strptime(view_date_str, "%Y-%m-%d").date()
        else:
            view_date = timezone.localdate()
    except Exception:
        view_date = timezone.localdate()

    # bookings visible to supervisor (not archived by supervisor)
    bookings = Booking.objects.filter(is_archived_supervisor=False).order_by("-date", "-created_at")
    archived_bookings = Booking.objects.filter(is_archived_supervisor=True).order_by("-date", "-created_at")

    total = bookings.count()
    pending = bookings.filter(status="pending").count()
    in_progress = bookings.filter(status="in_progress").count()
    completed = bookings.filter(status="completed").count()

    # prepare timeslots with per-date booked counts & availability (for display)
    timeslot_qs = TimeSlot.objects.all().order_by("start_time")
    bookings_per_slot_qs = (
        Booking.objects
        .filter(time_slot__isnull=False, date=view_date)
        .values("time_slot")
        .annotate(cnt=Count("id"))
    )
    bookings_per_slot = {item["time_slot"]: item["cnt"] for item in bookings_per_slot_qs}

    timeslots = []
    for slot in timeslot_qs:
        booked_count = bookings_per_slot.get(slot.id, 0)
        slot.booked_count = booked_count
        slot.capacity_effective = _default_capacity(slot)
        slot.is_available_for_date = booked_count < slot.capacity_effective
        timeslots.append(slot)

    # active announcement (if any)
    today = timezone.localdate()
    if hasattr(Announcement, "expiry"):
        announcement = Announcement.objects.filter(is_active=True, expiry__gte=today).order_by("-created_at").first()
    else:
        announcement = Announcement.objects.filter(is_active=True).order_by("-created_at").first()


    context = {
        "bookings": bookings,
        "archived_bookings": archived_bookings,
        "total": total,
        "pending": pending,
        "in_progress": in_progress,
        "completed": completed,
        "timeslots": timeslots,
        "announcement": announcement,
        "view_date": view_date,
    }
    return render(request, "supervisor_dashboard.html", context)


@login_required
@user_passes_test(is_supervisor)
def update_booking_status(request, booking_id):
    """
    Change booking.status (POST) and re-evaluate time slot availability for that booking's date.
    """
    booking = get_object_or_404(Booking, id=booking_id)

    if request.method == "POST":
        new_status = request.POST.get("status")
        if new_status not in dict(Booking.STATUS_CHOICES):
            messages.error(request, "Invalid status selected.")
            return redirect("bookings:supervisor_dashboard")

        booking.status = new_status
        booking.save()

        # re-evaluate the slot availability for the date of this booking
        slot = getattr(booking, "time_slot", None)
        if slot:
            occupied = slot.bookings.filter(date=booking.date).exclude(status="cancelled").count()
            capacity = _default_capacity(slot)

            # If occupied >= capacity -> ensure global flag reflects full (best-effort)
            if occupied >= capacity:
                if slot.is_available:
                    slot.is_available = False
                    slot.save(update_fields=["is_available"])
            else:
                if not slot.is_available:
                    slot.is_available = True
                    slot.save(update_fields=["is_available"])

        messages.success(request, f"Booking status updated to {booking.get_status_display()}")
        return redirect("bookings:supervisor_dashboard")

    # GET fallback: show a simple status form
    status_choices = Booking.STATUS_CHOICES
    return render(request, "update_booking.html", {
        "booking": booking,
        "status_choices": status_choices,
    })


@login_required
def cancel_booking(request, booking_id):
    """
    Customer cancels a booking (only allowed for their own pending bookings).
    We update booking.status and re-evaluate slot global availability for that date.
    """
    booking = get_object_or_404(Booking, id=booking_id, customer=request.user)

    if booking.status != "pending":
        messages.warning(request, "Only pending bookings can be cancelled.")
        return redirect("bookings:my_bookings")

    # cancel the booking
    booking.status = "cancelled"
    booking.save()

    # update slot availability for the booking date (best-effort)
    slot = getattr(booking, "time_slot", None)
    if slot:
        occupied = slot.bookings.filter(date=booking.date).exclude(status="cancelled").count()
        capacity = _default_capacity(slot)
        if occupied < capacity and not slot.is_available:
            slot.is_available = True
            slot.save(update_fields=["is_available"])

    messages.success(request, "Your booking has been cancelled successfully.")
    return redirect("bookings:my_bookings")


@login_required
def archive_booking_user(request, booking_id):
    """
    Customer archives (soft-delete from their view) a completed/cancelled booking.
    """
    booking = get_object_or_404(Booking, id=booking_id, customer=request.user)
    if booking.status not in ["completed", "cancelled"]:
        messages.error(request, "Only completed or cancelled bookings can be archived.")
        return redirect("bookings:my_bookings")

    if not booking.is_archived_customer:
        booking.is_archived_customer = True
        booking.customer_archived_at = timezone.now()
        booking.customer_archived_by = request.user
        booking.save(update_fields=["is_archived_customer", "customer_archived_at", "customer_archived_by"])

    return redirect("bookings:my_bookings")


@login_required
@user_passes_test(is_supervisor)
def archive_booking_supervisor(request, booking_id):
    """
    Supervisor archives a completed/cancelled booking (soft-delete from supervisor view).
    """
    booking = get_object_or_404(Booking, id=booking_id)

    if booking.status not in ["completed", "cancelled"]:
        messages.error(request, "Only completed or cancelled bookings can be archived.")
        return redirect("bookings:supervisor_dashboard")

    if not booking.is_archived_supervisor:
        booking.is_archived_supervisor = True
        booking.supervisor_archived_at = timezone.now()
        booking.supervisor_archived_by = request.user
        booking.save(update_fields=["is_archived_supervisor", "supervisor_archived_at", "supervisor_archived_by"])

    return redirect("bookings:supervisor_dashboard")


@login_required
def delete_booking(request, booking_id):
    """
    Soft-delete for supervisor (keeps a record, marks archived_supervisor).
    """
    booking = get_object_or_404(Booking, id=booking_id)

    if booking.status not in ["completed", "cancelled"]:
        messages.error(request, "Only completed or cancelled bookings can be archived/deleted.")
        return redirect("bookings:supervisor_dashboard")

    if not booking.is_archived_supervisor:
        booking.is_archived_supervisor = True
        booking.supervisor_archived_at = timezone.now()
        if request.user.is_authenticated:
            booking.supervisor_archived_by = request.user
        booking.save(update_fields=["is_archived_supervisor", "supervisor_archived_at", "supervisor_archived_by"])

    return redirect("bookings:supervisor_dashboard")


@login_required
@user_passes_test(is_supervisor)
def manage_time_slots(request):
    """
    Manage time slots: generate, add, toggle (global), delete, update capacity.
    Shows per-slot booked_count and availability FOR the selected date (view_date).
    """
    # Determine view_date (GET first, fallback to POST.view_date, default today)
    view_date_str = request.GET.get("view_date") or request.POST.get("view_date")
    try:
        if view_date_str:
            view_date = datetime.strptime(view_date_str, "%Y-%m-%d").date()
        else:
            view_date = timezone.localdate()
    except Exception:
        view_date = timezone.localdate()

    # Fetch timeslots and compute booked counts for the selected date
    timeslot_qs = TimeSlot.objects.all().order_by("start_time")
    bookings_per_slot_qs = (
        Booking.objects
        .filter(time_slot__isnull=False, date=view_date)
        .values("time_slot")
        .annotate(cnt=Count("id"))
    )
    bookings_per_slot = {item["time_slot"]: item["cnt"] for item in bookings_per_slot_qs}

    timeslots = []
    for slot in timeslot_qs:
        booked_count = bookings_per_slot.get(slot.id, 0)
        slot.booked_count = booked_count

        # determine capacity safely
        slot.capacity_effective = _default_capacity(slot)

        # availability FOR the selected date (True if booked_count < capacity)
        slot.is_available_for_date = (booked_count < slot.capacity_effective)

        # include global availability flag (slot.is_available) for toggles
        timeslots.append(slot)

    # POST handling (multiple actions)
    if request.method == "POST":
        post_view_date = request.POST.get("view_date") or view_date_str

        # Generate default slots (09:00 -> 18:00, 30-min, skip 13:00-14:00)
        if "generate_slots" in request.POST:
            start_dt = datetime.combine(datetime.today(), time(hour=9, minute=0))
            end_dt = datetime.combine(datetime.today(), time(hour=18, minute=0))
            slot_length = timedelta(minutes=30)

            created = 0
            current = start_dt
            while current < end_dt:
                slot_start = current.time()
                slot_end = (current + slot_length).time()

                # Skip lunch 13:00-14:00
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

            redirect_url = reverse("bookings:manage_time_slots")
            if post_view_date:
                redirect_url += f"?view_date={post_view_date}"
            return redirect(redirect_url)

        # Add new slot
        if "add_slot" in request.POST:
            start = request.POST.get("start_time")
            end = request.POST.get("end_time")
            note = request.POST.get("note", "").strip() or None

            if not start or not end:
                messages.error(request, "Please provide both start and end times for the slot.")
            else:
                try:
                    start_parsed = datetime.strptime(start, "%H:%M").time()
                    end_parsed = datetime.strptime(end, "%H:%M").time()
                except Exception:
                    messages.error(request, "Invalid time format. Use HH:MM.")
                    redirect_url = reverse("bookings:manage_time_slots")
                    if post_view_date:
                        redirect_url += f"?view_date={post_view_date}"
                    return redirect(redirect_url)

                if TimeSlot.objects.filter(start_time=start_parsed, end_time=end_parsed).exists():
                    messages.warning(request, "A slot with the same start and end times already exists.")
                else:
                    TimeSlot.objects.create(start_time=start_parsed, end_time=end_parsed, note=note, capacity=2)
                    messages.success(request, "Time slot added successfully.")

            redirect_url = reverse("bookings:manage_time_slots")
            if post_view_date:
                redirect_url += f"?view_date={post_view_date}"
            return redirect(redirect_url)

        # Toggle global availability flag
        if "toggle" in request.POST:
            slot_id = request.POST.get("slot_id")
            slot = get_object_or_404(TimeSlot, id=slot_id)
            slot.is_available = not slot.is_available
            slot.save(update_fields=["is_available"])
            messages.success(request, "Slot availability (global) toggled.")
            redirect_url = reverse("bookings:manage_time_slots")
            if post_view_date:
                redirect_url += f"?view_date={post_view_date}"
            return redirect(redirect_url)

        # Delete slot (only if no bookings for selected date)
        if "delete" in request.POST:
            slot_id = request.POST.get("slot_id")
            slot = get_object_or_404(TimeSlot, id=slot_id)
            booked_count = Booking.objects.filter(time_slot=slot, date=view_date).count()
            if booked_count > 0:
                messages.error(request, "Cannot delete a slot that has bookings for the selected date.")
            else:
                slot.delete()
                messages.success(request, "Slot deleted.")
            redirect_url = reverse("bookings:manage_time_slots")
            if post_view_date:
                redirect_url += f"?view_date={post_view_date}"
            return redirect(redirect_url)

        # Update capacity
        if "update_capacity" in request.POST:
            slot_id = request.POST.get("slot_id")
            new_capacity = request.POST.get("capacity")
            try:
                new_capacity_int = int(new_capacity)
                if new_capacity_int < 1:
                    raise ValueError
            except Exception:
                messages.error(request, "Capacity must be a positive integer.")
                redirect_url = reverse("bookings:manage_time_slots")
                if post_view_date:
                    redirect_url += f"?view_date={post_view_date}"
                return redirect(redirect_url)

            slot = get_object_or_404(TimeSlot, id=slot_id)
            slot.capacity = new_capacity_int
            slot.save(update_fields=["capacity"])
            messages.success(request, "Slot capacity updated.")

            # Re-evaluate global availability for chosen date (best-effort)
            occupied = Booking.objects.filter(time_slot=slot, date=view_date).exclude(status="cancelled").count()
            if occupied >= slot.capacity and slot.is_available:
                slot.is_available = False
                slot.save(update_fields=["is_available"])

            redirect_url = reverse("bookings:manage_time_slots")
            if post_view_date:
                redirect_url += f"?view_date={post_view_date}"
            return redirect(redirect_url)

    # GET render
    return render(request, "manage_slots.html", {
        "timeslots": timeslots,
        "view_date": view_date,
    })


@login_required
@user_passes_test(is_supervisor)
def announcement_manager(request):
    """
    Supervisor can create/update a single announcement (and view recent ones).
    Returns: form, announcement (latest active), recent_announcements, announcements (all for template)
    """
    # latest announcement instance (single editable object), or None
    announcement = Announcement.objects.order_by("-created_at").first()

    if request.method == "POST":
        # If you have an AnnouncementForm that maps to the model, use it.
        form = AnnouncementForm(request.POST, instance=announcement)
        if form.is_valid():
            form.save()
            messages.success(request, "Announcement saved.")
            return redirect("bookings:announcement_manager")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = AnnouncementForm(instance=announcement)

    # recent announcements list (for dashboard display)
    recent_announcements = Announcement.objects.order_by("-created_at")[:10]

    # full announcements list (template previously expected variable named 'announcements')
    announcements = Announcement.objects.all().order_by("-created_at")

    # optional: pass 'today' if your template compares expiries
    today = timezone.localdate()

    return render(request, "announcement_manager.html", {
        "form": form,
        "announcement": announcement,
        "recent_announcements": recent_announcements,
        "announcements": announcements,
        "today": today,
    })

@login_required
@user_passes_test(is_supervisor)
def delete_announcement(request, pk):
    ann = get_object_or_404(Announcement, pk=pk)
    if request.method == "POST":
        ann.delete()
        messages.success(request, "Announcement deleted.")
        # redirect back to manager or dashboard
        return redirect(reverse('bookings:announcement_manager'))
    # if GET, redirect back or show a confirmation page; here we redirect:
    messages.warning(request, "Please confirm deletion using the form.")
    return redirect(reverse('bookings:announcement_manager'))

