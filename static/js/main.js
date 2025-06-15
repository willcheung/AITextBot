// Main JavaScript file for Calendar AI application
document.addEventListener('DOMContentLoaded', function() {
    // Initialize Feather icons
    if (typeof feather !== 'undefined') {
        feather.replace();
    }
    
    // Auto-resize textarea
    const textareas = document.querySelectorAll('textarea');
    textareas.forEach(textarea => {
        textarea.addEventListener('input', autoResize);
        autoResize.call(textarea); // Initial resize
    });
    
    // Add fade-in animation to cards
    const cards = document.querySelectorAll('.event-card, .feature-card, .input-card');
    cards.forEach((card, index) => {
        card.style.animationDelay = `${index * 0.1}s`;
        card.classList.add('fade-in');
    });
    
    // Form validation
    const forms = document.querySelectorAll('form');
    forms.forEach(form => {
        form.addEventListener('submit', function(e) {
            if (!validateForm(this)) {
                e.preventDefault();
            }
        });
    });
    
    // Auto-dismiss alerts after 5 seconds
    const alerts = document.querySelectorAll('.alert:not(.alert-permanent)');
    alerts.forEach(alert => {
        setTimeout(() => {
            if (alert.parentNode) {
                alert.remove();
            }
        }, 5000);
    });
    
    // Confirm delete actions
    const deleteButtons = document.querySelectorAll('button[title*="Delete"], button[onclick*="confirm"]');
    deleteButtons.forEach(button => {
        button.addEventListener('click', function(e) {
            if (!confirm('Are you sure you want to delete this item?')) {
                e.preventDefault();
                return false;
            }
        });
    });
    
    // Loading states for forms
    const submitButtons = document.querySelectorAll('button[type="submit"]');
    submitButtons.forEach(button => {
        button.closest('form').addEventListener('submit', function() {
            button.disabled = true;
            const originalText = button.innerHTML;
            button.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status"></span>Processing...';
            
            // Re-enable after 30 seconds as failsafe
            setTimeout(() => {
                button.disabled = false;
                button.innerHTML = originalText;
            }, 30000);
        });
    });
    
    // Smooth scrolling for anchor links
    const anchorLinks = document.querySelectorAll('a[href^="#"]');
    anchorLinks.forEach(link => {
        link.addEventListener('click', function(e) {
            const targetId = this.getAttribute('href').substring(1);
            const targetElement = document.getElementById(targetId);
            
            if (targetElement) {
                e.preventDefault();
                targetElement.scrollIntoView({
                    behavior: 'smooth',
                    block: 'start'
                });
            }
        });
    });
    
    // Copy to clipboard functionality (for future use)
    const copyButtons = document.querySelectorAll('[data-copy]');
    copyButtons.forEach(button => {
        button.addEventListener('click', function() {
            const textToCopy = this.dataset.copy;
            navigator.clipboard.writeText(textToCopy).then(() => {
                showToast('Copied to clipboard!', 'success');
            }).catch(() => {
                showToast('Failed to copy to clipboard', 'error');
            });
        });
    });
    
    // Auto-save draft functionality for text input (future enhancement)
    const textInput = document.getElementById('text');
    if (textInput) {
        let saveTimeout;
        textInput.addEventListener('input', function() {
            clearTimeout(saveTimeout);
            saveTimeout = setTimeout(() => {
                saveDraft(this.value);
            }, 2000); // Save after 2 seconds of inactivity
        });
        
        // Load draft on page load
        loadDraft();
    }
});

// Helper function to auto-resize textareas
function autoResize() {
    this.style.height = 'auto';
    this.style.height = this.scrollHeight + 'px';
}

// Form validation function
function validateForm(form) {
    let isValid = true;
    const requiredFields = form.querySelectorAll('[required]');
    
    requiredFields.forEach(field => {
        if (!field.value.trim()) {
            field.classList.add('is-invalid');
            isValid = false;
        } else {
            field.classList.remove('is-invalid');
        }
    });
    
    // Date validation
    const startDate = form.querySelector('[name="start_date"]');
    const endDate = form.querySelector('[name="end_date"]');
    
    if (startDate && endDate && startDate.value && endDate.value) {
        if (new Date(startDate.value) > new Date(endDate.value)) {
            endDate.classList.add('is-invalid');
            showToast('End date cannot be before start date', 'error');
            isValid = false;
        } else {
            endDate.classList.remove('is-invalid');
        }
    }
    
    // Time validation
    const startTime = form.querySelector('[name="start_time"]');
    const endTime = form.querySelector('[name="end_time"]');
    
    if (startTime && endTime && startTime.value && endTime.value && 
        startDate && endDate && startDate.value === endDate.value) {
        if (startTime.value >= endTime.value) {
            endTime.classList.add('is-invalid');
            showToast('End time must be after start time', 'error');
            isValid = false;
        } else {
            endTime.classList.remove('is-invalid');
        }
    }
    
    return isValid;
}

// Toast notification function
function showToast(message, type = 'info') {
    const toastContainer = getOrCreateToastContainer();
    
    const toast = document.createElement('div');
    toast.className = `alert alert-${type} alert-dismissible fade show`;
    toast.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    
    toastContainer.appendChild(toast);
    
    // Auto-remove after 5 seconds
    setTimeout(() => {
        if (toast.parentNode) {
            toast.remove();
        }
    }, 5000);
}

// Get or create toast container
function getOrCreateToastContainer() {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.style.position = 'fixed';
        container.style.top = '20px';
        container.style.right = '20px';
        container.style.zIndex = '9999';
        container.style.maxWidth = '300px';
        document.body.appendChild(container);
    }
    return container;
}

// Draft saving functionality
function saveDraft(content) {
    if (content.trim()) {
        localStorage.setItem('calendar-ai-draft', content);
    } else {
        localStorage.removeItem('calendar-ai-draft');
    }
}

function loadDraft() {
    const textInput = document.getElementById('text');
    const draft = localStorage.getItem('calendar-ai-draft');
    
    if (textInput && draft && !textInput.value.trim()) {
        textInput.value = draft;
        autoResize.call(textInput);
        
        // Show notification about loaded draft
        showToast('Draft loaded from previous session', 'info');
    }
}

function clearDraft() {
    localStorage.removeItem('calendar-ai-draft');
}

// Utility function to format dates for display
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'long',
        day: 'numeric'
    });
}

// Utility function to format times for display
function formatTime(timeString) {
    const [hours, minutes] = timeString.split(':');
    const date = new Date();
    date.setHours(parseInt(hours), parseInt(minutes));
    
    return date.toLocaleTimeString('en-US', {
        hour: 'numeric',
        minute: '2-digit',
        hour12: true
    });
}

// Event listener for real-time form field updates
document.addEventListener('input', function(e) {
    if (e.target.classList.contains('is-invalid')) {
        e.target.classList.remove('is-invalid');
    }
});

// Keyboard shortcuts
document.addEventListener('keydown', function(e) {
    // Ctrl/Cmd + Enter to submit forms
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        const activeForm = document.activeElement.closest('form');
        if (activeForm) {
            const submitButton = activeForm.querySelector('button[type="submit"]');
            if (submitButton) {
                submitButton.click();
            }
        }
    }
    
    // Escape to close modals or go back
    if (e.key === 'Escape') {
        const backButton = document.querySelector('.btn[href*="dashboard"]');
        if (backButton && window.location.pathname.includes('edit')) {
            window.history.back();
        }
    }
});

// Function to start Google login with timezone detection
window.startGoogleLogin = function() {
    try {
        // Get timezone name
        const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
        
        console.log('Detected timezone:', timezone);
        
        // Create the login URL with timezone parameter
        const loginUrl = '/google_login';
        const redirectUrl = `${loginUrl}?timezone=${encodeURIComponent(timezone)}`;
        
        console.log('Redirecting to:', redirectUrl);
        window.location.href = redirectUrl;
    } catch (error) {
        console.error('Error detecting timezone:', error);
        // Fallback to login without timezone
        window.location.href = '/google_login';
    }
}

// Initialize tooltips (if Bootstrap tooltips are needed in the future)
function initializeTooltips() {
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
}

// Performance: Lazy load non-critical features
window.addEventListener('load', function() {
    // Initialize tooltips after page load
    if (typeof bootstrap !== 'undefined') {
        initializeTooltips();
    }
    
    // Refresh Feather icons in case any were added dynamically
    if (typeof feather !== 'undefined') {
        feather.replace();
    }
});

// Error handling for failed AJAX requests (future use)
window.addEventListener('unhandledrejection', function(e) {
    console.error('Unhandled promise rejection:', e.reason);
    showToast('An unexpected error occurred. Please try again.', 'error');
});

// Service worker registration (future PWA enhancement)
if ('serviceWorker' in navigator) {
    window.addEventListener('load', function() {
        // Service worker code would go here for offline functionality
    });
}
