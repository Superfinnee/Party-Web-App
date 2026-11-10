// Show or hide the given password fields. Defaults to the field with id "password".
function togglePassword(...ids) {
    if (ids.length === 0) ids = ['password'];
    ids.forEach(id => {
        const field = document.getElementById(id);
        if (field) field.type = field.type === 'password' ? 'text' : 'password';
    });
}

// Register page: when a guest says they aren't attending, hide the catering
// questions and stop requiring them.
document.querySelectorAll('.guest-card').forEach(card => {
    const section = card.querySelector('.attending-only');
    if (!section) return;

    const update = () => {
        const choice = card.querySelector('.attending-toggle:checked');
        const attending = !choice || choice.value === 'yes';
        section.hidden = !attending;
        section.querySelectorAll('[data-required]').forEach(input => {
            input.required = attending;
        });
    };

    card.querySelectorAll('.attending-toggle').forEach(radio => {
        radio.addEventListener('change', update);
    });
    update();
});


// Register page: the "Need to bring more people?" panel. Its fields are
// disabled while it's closed so an abandoned request isn't sent.
document.querySelectorAll('.extra-request').forEach(card => {
    const toggle = card.querySelector('.extra-request-toggle');
    const panel = card.querySelector('.extra-request-panel');
    if (!toggle || !panel) return;

    toggle.addEventListener('click', () => {
        const opening = panel.hidden;
        panel.hidden = !opening;
        panel.querySelectorAll('input, textarea').forEach(field => {
            field.disabled = !opening;
        });
        toggle.setAttribute('aria-expanded', String(opening));
        toggle.textContent = opening ? 'Never mind, remove my request' : 'Ask the host for more spots';
        if (opening) panel.querySelector('input, textarea')?.focus();
    });
});
