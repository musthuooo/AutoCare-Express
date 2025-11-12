from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth import get_user_model
from .forms import CustomUserCreationForm, CustomUserUpdateForm, ProfileUpdateForm
from .models import Profile

User = get_user_model()


def home(request):
    return render(request, 'home.html')

def about(request):
    return render(request, 'about.html')

def services(request):
    return render(request, 'services.html')

def contact(request):
    return render(request, 'contact.html')


@login_required
def profile(request):
    if not hasattr(request.user, 'profile'):
        Profile.objects.create(user=request.user)

    return render(request, 'profile.html', {'user': request.user})


@login_required
def edit_profile(request):
    if not hasattr(request.user, 'profile'):
        Profile.objects.create(user=request.user)

    if request.method == 'POST':
        user_form = CustomUserUpdateForm(request.POST, instance=request.user)
        profile_form = ProfileUpdateForm(request.POST, instance=request.user.profile)

        if user_form.is_valid() and profile_form.is_valid():
            user_form.save()
            profile_form.save()
            messages.success(request, 'Your profile has been updated successfully!')
            return redirect('users:profile')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        user_form = CustomUserUpdateForm(instance=request.user)
        profile_form = ProfileUpdateForm(instance=request.user.profile)

    context = {
        'user_form': user_form,
        'profile_form': profile_form
    }
    return render(request, 'edit_profile.html', context)


def signup_view(request):
    if request.user.is_authenticated:
        return redirect('users:dashboard')

    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)

            # ✅ Always set the role to "customer"
            user.role = 'customer'
            user.save()

            # Ensure profile exists
            if not hasattr(user, 'profile'):
                Profile.objects.create(user=user)

            # Log the user in automatically
            login(request, user)
            messages.success(request, "Account created successfully! Please complete your profile details.")

            # Redirect to Edit Profile page after signup
            return redirect('users:edit_profile')

        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = CustomUserCreationForm()

    return render(request, 'signup.html', {'form': form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('users:dashboard')

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)

        if user:
            login(request, user)

            if not hasattr(user, 'profile'):
                Profile.objects.create(user=user)

            messages.success(request, f"Welcome back, {user.username}!")
            return redirect('users:dashboard')
        else:
            messages.error(request, "Invalid username or password.")

    return render(request, 'login.html')


def logout_view(request):
    logout(request)
    messages.info(request, "You have been logged out successfully.")
    return redirect('users:home')


@login_required
def dashboard_view(request):
    user = request.user

    if user.role == 'customer':
        return redirect('bookings:my_bookings')
    elif user.role == 'supervisor':
        return redirect('bookings:supervisor_dashboard')
    else:
        messages.error(request, "Your account role is not recognized.")
        return redirect('users:login')
