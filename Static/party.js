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
