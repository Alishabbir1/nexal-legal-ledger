/**
 * Global password visibility toggle.
 * Eye icon shows on focus, hides on blur. Toggles type on icon click.
 */
(function() {
    var EYE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
    var EYE_OFF_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';

    function initPasswordToggles() {
        var inputs = document.querySelectorAll('input[type="password"]');
        for (var i = 0; i < inputs.length; i++) {
            var inp = inputs[i];
            if (inp.closest('.pwd-input-wrap') || inp.hasAttribute('data-pwd-toggle-init')) continue;
            inp.setAttribute('data-pwd-toggle-init', '1');

            var wrap = document.createElement('div');
            wrap.className = 'pwd-input-wrap';
            inp.parentNode.insertBefore(wrap, inp);
            wrap.appendChild(inp);

            inp.style.paddingRight = '40px';

            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'pwd-toggle';
            btn.title = 'Toggle visibility';
            btn.setAttribute('aria-label', 'Toggle password visibility');
            btn.innerHTML = EYE_SVG;
            btn.addEventListener('click', function(ev) {
                ev.preventDefault();
                var input = this.previousElementSibling;
                if (!input || (input.type !== 'password' && input.type !== 'text')) return;
                var isPwd = input.type === 'password';
                input.type = isPwd ? 'text' : 'password';
                this.innerHTML = isPwd ? EYE_OFF_SVG : EYE_SVG;
            });

            (function(w) {
                inp.addEventListener('focus', function() {
                    w.classList.add('pwd-focused');
                });
                inp.addEventListener('blur', function() {
                    if (!w) return;
                    setTimeout(function() {
                        w.classList.remove('pwd-focused');
                    }, 150);
                });
            })(wrap);
            wrap.appendChild(btn);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initPasswordToggles);
    } else {
        initPasswordToggles();
    }

    window.initPasswordToggles = initPasswordToggles;
})();
