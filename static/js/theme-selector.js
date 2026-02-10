// Global Snakes theme runtime + hero theme selector interactions.
(function () {
    'use strict';

    var STORAGE_KEY = 'theme';
    var DEFAULT_SLUG = 'default';
    var THEMES = [
        { name: 'Default', slug: 'default', swatch1: '#667eea', swatch2: '#764ba2' },
        { name: 'Aurora Mint', slug: 'aurora-mint', swatch1: '#3dd6b0', swatch2: '#0f766e' },
        { name: 'Emberforge', slug: 'emberforge', swatch1: '#ff7a3d', swatch2: '#c0392b' },
        { name: 'Midnight Violet', slug: 'midnight-violet', swatch1: '#8b5cf6', swatch2: '#4c1d95' },
        { name: 'Solar Flare', slug: 'solar-flare', swatch1: '#ffb703', swatch2: '#fb8500' },
        { name: 'Oceanic Depths', slug: 'oceanic-depths', swatch1: '#00b4d8', swatch2: '#0077b6' },
        { name: 'Rose Nebula', slug: 'rose-nebula', swatch1: '#ff5ca8', swatch2: '#c9184a' },
        { name: 'Cyber Lime', slug: 'cyber-lime', swatch1: '#a3e635', swatch2: '#4d7c0f' },
        { name: 'Glacier Blue', slug: 'glacier-blue', swatch1: '#60a5fa', swatch2: '#1d4ed8' },
        { name: 'Sandstone Gold', slug: 'sandstone-gold', swatch1: '#d4a373', swatch2: '#a97142' }
    ];

    var THEMES_BY_SLUG = {};
    THEMES.forEach(function (theme) {
        THEMES_BY_SLUG[theme.slug] = theme;
    });

    function slugify(value) {
        return String(value || '')
            .trim()
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, '-')
            .replace(/^-+|-+$/g, '');
    }

    function normalizeTheme(rawValue) {
        if (!rawValue) return DEFAULT_SLUG;
        var slug = slugify(rawValue);
        return THEMES_BY_SLUG[slug] ? slug : DEFAULT_SLUG;
    }

    function getStoredTheme() {
        var storedTheme = '';
        try {
            storedTheme = localStorage.getItem(STORAGE_KEY) || localStorage.theme || '';
        } catch (e) {
            storedTheme = '';
        }
        return normalizeTheme(storedTheme);
    }

    function setThemeAttribute(slug) {
        var resolved = THEMES_BY_SLUG[slug] ? slug : DEFAULT_SLUG;
        if (document.documentElement) {
            document.documentElement.dataset.theme = resolved;
        }
        if (document.body) {
            document.body.dataset.theme = resolved;
        }
        return resolved;
    }

    function updateSelectedState(slug) {
        var options = document.querySelectorAll('.theme-option');
        options.forEach(function (option) {
            var isSelected = option.getAttribute('data-theme') === slug;
            option.classList.toggle('is-selected', isSelected);
            option.setAttribute('aria-checked', isSelected ? 'true' : 'false');
        });
    }

    function persistTheme(slug) {
        var theme = THEMES_BY_SLUG[slug] || THEMES_BY_SLUG[DEFAULT_SLUG];
        try {
            localStorage.setItem(STORAGE_KEY, theme.name);
            localStorage.theme = theme.name;
        } catch (e) {
            // Ignore storage failures (private mode, quota, etc).
        }
    }

    function applyTheme(slug, persist) {
        var resolved = THEMES_BY_SLUG[slug] ? slug : DEFAULT_SLUG;
        setThemeAttribute(resolved);
        updateSelectedState(resolved);
        if (persist !== false) {
            persistTheme(resolved);
        }
        return resolved;
    }

    function getOptionScope(option) {
        return option.closest('[data-theme-options-group]') || option.closest('.theme-options') || option.parentElement;
    }

    function moveFocus(current, step) {
        var scope = getOptionScope(current);
        if (!scope) return;
        var options = Array.prototype.slice.call(scope.querySelectorAll('.theme-option'));
        var currentIndex = options.indexOf(current);
        if (currentIndex < 0 || !options.length) return;
        var nextIndex = (currentIndex + step + options.length) % options.length;
        options[nextIndex].focus();
    }

    function bindEvents() {
        var options = document.querySelectorAll('.theme-option');
        if (!options.length) return;

        options.forEach(function (option) {
            if (option.dataset.themeBound === '1') return;
            option.dataset.themeBound = '1';

            option.addEventListener('click', function () {
                applyTheme(option.getAttribute('data-theme'));
            });

            option.addEventListener('keydown', function (event) {
                if (event.key === 'ArrowDown' || event.key === 'ArrowRight') {
                    event.preventDefault();
                    moveFocus(option, 1);
                } else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') {
                    event.preventDefault();
                    moveFocus(option, -1);
                }
            });
        });
    }

    function shouldRenderQuickSelector() {
        if (!document.body) return false;
        return document.body.classList.contains('snakes-theme') || !!document.querySelector('.snakes-theme');
    }

    function createThemeOption(theme) {
        var option = document.createElement('button');
        option.type = 'button';
        option.className = 'theme-option';
        option.setAttribute('role', 'radio');
        option.setAttribute('aria-checked', 'false');
        option.setAttribute('data-theme', theme.slug);
        option.setAttribute('data-theme-name', theme.name);
        option.setAttribute('aria-label', theme.name + ' theme');

        var swatch = document.createElement('span');
        swatch.className = 'theme-swatch';
        swatch.setAttribute('aria-hidden', 'true');
        swatch.style.setProperty('--swatch-1', theme.swatch1);
        swatch.style.setProperty('--swatch-2', theme.swatch2);

        var label = document.createElement('span');
        label.className = 'theme-option-label';
        label.textContent = theme.name;

        var check = document.createElement('span');
        check.className = 'theme-check';
        check.setAttribute('aria-hidden', 'true');
        check.textContent = '✓';

        option.appendChild(swatch);
        option.appendChild(label);
        option.appendChild(check);
        return option;
    }

    function ensureQuickSelector() {
        if (!shouldRenderQuickSelector()) return;
        if (document.getElementById('theme-quick-launch')) return;

        var launchBtn = document.createElement('button');
        launchBtn.type = 'button';
        launchBtn.id = 'theme-quick-launch';
        launchBtn.className = 'theme-quick-launch';
        launchBtn.setAttribute('aria-label', 'Open theme selector');
        launchBtn.setAttribute('aria-controls', 'theme-selector-modal');
        launchBtn.setAttribute('aria-expanded', 'false');
        launchBtn.innerHTML = '<span aria-hidden="true">🎨</span>';

        var overlay = document.createElement('div');
        overlay.id = 'theme-selector-modal';
        overlay.className = 'theme-selector-modal-overlay';
        overlay.setAttribute('aria-hidden', 'true');

        var modal = document.createElement('div');
        modal.className = 'theme-selector-modal';
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.setAttribute('aria-labelledby', 'theme-selector-modal-title');

        var header = document.createElement('div');
        header.className = 'theme-selector-modal-header';

        var title = document.createElement('h3');
        title.id = 'theme-selector-modal-title';
        title.textContent = 'Themes';

        var closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'theme-selector-modal-close';
        closeBtn.setAttribute('aria-label', 'Close theme selector');
        closeBtn.textContent = '×';

        header.appendChild(title);
        header.appendChild(closeBtn);

        var subtitle = document.createElement('p');
        subtitle.className = 'theme-selector-modal-subtitle';
        subtitle.textContent = 'Choose your game color style.';

        var optionsWrap = document.createElement('div');
        optionsWrap.className = 'theme-options';
        optionsWrap.setAttribute('data-theme-options-group', 'quick');
        optionsWrap.setAttribute('role', 'radiogroup');
        optionsWrap.setAttribute('aria-label', 'Theme options');
        THEMES.forEach(function (theme) {
            optionsWrap.appendChild(createThemeOption(theme));
        });

        modal.appendChild(header);
        modal.appendChild(subtitle);
        modal.appendChild(optionsWrap);
        overlay.appendChild(modal);

        function closeModal() {
            overlay.classList.remove('is-open');
            overlay.setAttribute('aria-hidden', 'true');
            launchBtn.setAttribute('aria-expanded', 'false');
        }

        function openModal() {
            overlay.classList.add('is-open');
            overlay.setAttribute('aria-hidden', 'false');
            launchBtn.setAttribute('aria-expanded', 'true');
            var selected = overlay.querySelector('.theme-option.is-selected') || overlay.querySelector('.theme-option');
            if (selected) selected.focus();
        }

        launchBtn.addEventListener('click', openModal);
        closeBtn.addEventListener('click', closeModal);
        overlay.addEventListener('click', function (event) {
            if (event.target === overlay) closeModal();
        });
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && overlay.classList.contains('is-open')) {
                closeModal();
            }
        });

        document.body.appendChild(launchBtn);
        document.body.appendChild(overlay);
    }

    var FAQ_BY_PAGE = {
        'mode-selection': {
            title: 'Mode Selection FAQ',
            intro: 'Choose a mode quickly with these tips.',
            faqs: [
                { q: 'Which mode should I start with?', a: 'If you want a guided challenge, start with Platformer Gauntlet. For combat practice, pick Boss Battle.' },
                { q: 'Do I keep bullets earned from modes?', a: 'Yes. Mode rewards are added to your total bullets and shown in your profile stats.' },
                { q: 'Why does a mode look unavailable?', a: 'Some modes can require player count, feature flags, or active sessions. Refresh after a moment and try again.' },
                { q: 'How do I return to the board?', a: 'Use Back/Leave controls in the mode, or return through the board links once you finish.' }
            ]
        },
        'game-board': {
            title: 'Board FAQ',
            intro: 'Key controls for this board view.',
            faqs: [
                { q: 'How do I move on the board?', a: 'Roll first, then complete the prompted step for that square to progress.' },
                { q: 'What are lesson squares?', a: 'Lesson squares open learning tasks tied to game progress and challenge unlocks.' },
                { q: 'Is progress saved automatically?', a: 'Yes. Core progress is stored with your account or session state.' },
                { q: 'Where can I see my rewards?', a: 'Track bullets and status in HUD/stat sections on board and mode pages.' }
            ]
        },
        'game-board-part1': {
            title: 'Board Part 1 FAQ',
            intro: 'Tips for early-game board progress.',
            faqs: [
                { q: 'What is the fastest way through Part 1?', a: 'Complete each lesson/question promptly and avoid skipping required checkpoints.' },
                { q: 'Can I leave and continue later?', a: 'Yes. Your progress is persisted, so you can return and continue from your state.' },
                { q: 'Why did my move not apply?', a: 'Moves only finalize after roll + required action flow completes for that square.' },
                { q: 'How do I unlock Part 2?', a: 'Finish the required Part 1 path and complete linked tasks to open the next board section.' }
            ]
        },
        'game-board-part2': {
            title: 'Board Part 2 FAQ',
            intro: 'Advanced board and challenge guidance.',
            faqs: [
                { q: 'What is different in Part 2?', a: 'Part 2 includes harder challenge sequencing and mode dependencies before final victory.' },
                { q: 'Do mode wins affect Part 2?', a: 'Yes. Completing required modes helps satisfy progression milestones.' },
                { q: 'How do I avoid losing progress?', a: 'Finish prompted actions per square and avoid closing mid-confirmation steps.' },
                { q: 'Where is the final completion shown?', a: 'After required progress is done, you are routed to the victory flow and Hall of Champions.' }
            ]
        },
        'boss-battle': {
            title: 'Boss Battle FAQ',
            intro: 'Combat-focused tips for the boss arena.',
            faqs: [
                { q: 'How do I survive longer?', a: 'Keep moving, watch attack patterns, and only commit to shots when lanes are safe.' },
                { q: 'Do bullets matter here?', a: 'Yes. Bullet economy and hit timing directly affect how fast you can finish the fight.' },
                { q: 'What happens when I lose?', a: 'You can retry from the mode flow without losing your champion record.' },
                { q: 'How do I exit the mode?', a: 'Use the in-game leave/exit control to return to board progression.' }
            ]
        },
        'pvp-arena': {
            title: 'PVP Arena FAQ',
            intro: 'Multiplayer match essentials.',
            faqs: [
                { q: 'How do I join a match quickly?', a: 'Use available room options and join active lobbies with open slots.' },
                { q: 'How is scoring handled?', a: 'Matches track score by configured arena rules and update standings during play.' },
                { q: 'What if connection is unstable?', a: 'Refresh once, rejoin your room, and avoid multiple tabs for the same account.' },
                { q: 'Can I leave without breaking progress?', a: 'Yes. Leave through in-mode controls so session cleanup completes correctly.' }
            ]
        },
        'slitherrush': {
            title: 'SLITHERRUSH FAQ',
            intro: 'Fast multiplayer slither combat tips.',
            faqs: [
                { q: 'How do I score points?', a: 'Collect energy orbs and survive longer than other slithers.' },
                { q: 'What causes elimination?', a: 'Crashing into walls, another body, or losing a head-to-head clash.' },
                { q: 'What happens after I die?', a: 'You immediately become a spectator and auto-follow a living slither.' },
                { q: 'How do I switch spectator targets?', a: 'Use Q/E while spectating to cycle through alive players.' }
            ]
        },
        'platformer-arcade': {
            title: 'Platformer FAQ',
            intro: 'Movement and objective guidance.',
            faqs: [
                { q: 'What are the controls?', a: 'Use A/D or Left/Right to move and Space to jump.' },
                { q: 'Where is the level exit?', a: 'The portal sits on top of the final platform in the route.' },
                { q: 'What happens if I fall?', a: 'You respawn and can immediately retry the run.' },
                { q: 'What is the completion reward?', a: 'Winning grants bonus bullets added to your total.' }
            ]
        },
        'victory': {
            title: 'Victory Page FAQ',
            intro: 'Post-win actions and leaderboard notes.',
            faqs: [
                { q: 'What does Hall of Champions do?', a: 'It records completed champions and shows ranked completion entries.' },
                { q: 'How do I continue from here?', a: 'Use Back to Board to resume board flow or Other Mini-Games for mode selection.' },
                { q: 'Why is my mode shown in stats?', a: 'The page reads your last recorded win mode from URL/session/local storage data.' },
                { q: 'Can I play again after winning?', a: 'Yes. You can return to board or enter another mode at any time.' }
            ]
        },
        'lesson1': {
            title: 'Lesson 1 FAQ',
            intro: 'Help for this lesson step.',
            faqs: [
                { q: 'How do I complete this lesson?', a: 'Read the prompt, finish required interaction blocks, then use the continue action.' },
                { q: 'What if a check does not pass?', a: 'Re-run the step exactly as instructed and confirm required fields are filled.' },
                { q: 'Can I revisit this lesson later?', a: 'Yes. Lesson pages can be reopened from board flow when needed.' },
                { q: 'Does lesson completion affect progress?', a: 'Yes. Lessons are part of board progression and unlock chain.' }
            ]
        },
        'lesson2': {
            title: 'Lesson 2 FAQ',
            intro: 'Help for this lesson step.',
            faqs: [
                { q: 'What is the goal of this lesson?', a: 'Complete the interactive task and submit according to the lesson instructions.' },
                { q: 'How do I know it saved?', a: 'Successful submission and navigation controls indicate completion was captured.' },
                { q: 'Can I retry if I made a mistake?', a: 'Yes. Reattempt the task and submit again before leaving.' },
                { q: 'Where do I go next?', a: 'Use the lesson navigation control to return to the board path.' }
            ]
        },
        'lesson3': {
            title: 'Lesson 3 FAQ',
            intro: 'Help for this lesson step.',
            faqs: [
                { q: 'How should I approach this lesson?', a: 'Follow each step in order and verify expected output before continuing.' },
                { q: 'Why am I blocked from continuing?', a: 'A required checkpoint is likely incomplete. Finish all required inputs first.' },
                { q: 'Will this change my game state?', a: 'Yes. Lesson milestones contribute to progression and unlocks.' },
                { q: 'Can I leave mid-lesson?', a: 'You can, but complete required steps before leaving to avoid repeating work.' }
            ]
        },
        'lesson4': {
            title: 'Lesson 4 FAQ',
            intro: 'Help for this lesson step.',
            faqs: [
                { q: 'What if content is not loading?', a: 'Refresh once and confirm your session is still active.' },
                { q: 'How do I pass this lesson quickly?', a: 'Focus on required prompts only, then submit and continue.' },
                { q: 'Can I check instructions again?', a: 'Yes. Review the top lesson section before resubmitting.' },
                { q: 'Does this affect leaderboard?', a: 'Indirectly. Lesson completion supports overall run completion state.' }
            ]
        },
        'lesson5': {
            title: 'Lesson 5 FAQ',
            intro: 'Help for this lesson step.',
            faqs: [
                { q: 'Is this the final lesson checkpoint?', a: 'This lesson is part of the final sequence before later progression steps.' },
                { q: 'How do I avoid losing my work?', a: 'Submit required inputs before navigating away from the page.' },
                { q: 'What comes after this lesson?', a: 'You return to board flow for the next unlock or challenge.' },
                { q: 'Can I repeat for practice?', a: 'Yes. Reopening lesson pages is supported for review.' }
            ]
        },
        question_template: {
            title: 'Question Page FAQ',
            intro: 'Answer and submission guidance.',
            faqs: [
                { q: 'How do I submit an answer?', a: 'Select or type your response, then use the submit/next control.' },
                { q: 'Why is Next disabled?', a: 'A required answer field is likely empty or not valid yet.' },
                { q: 'Can I retry a question?', a: 'Most question flows allow retries before final navigation.' },
                { q: 'How do I return to the board?', a: 'Use the built-in return/continue button at the end of the question flow.' }
            ]
        },
        'board-square': {
            title: 'Square Challenge FAQ',
            intro: 'Quick help for square-based mini challenges.',
            faqs: [
                { q: 'What is the objective here?', a: 'Complete the square challenge objective shown in the on-page instructions.' },
                { q: 'How do I exit this challenge?', a: 'Use provided continue/back controls after finishing the challenge step.' },
                { q: 'Do square challenges save progress?', a: 'Yes. Completion is tied to your board progression flow.' },
                { q: 'Can I retry if I fail?', a: 'Yes. Restart the square challenge and attempt again.' }
            ]
        },
        default: {
            title: 'Game Help FAQ',
            intro: 'Quick support for this page.',
            faqs: [
                { q: 'How do I continue my progress?', a: 'Use the page navigation controls and complete required tasks in order.' },
                { q: 'Is my progress saved?', a: 'Most pages persist game state through your account/session data.' },
                { q: 'Where can I find mode options?', a: 'Mode Selection is the central page for challenge-type mini-games.' },
                { q: 'What should I do if something looks stuck?', a: 'Refresh once and retry the page action after your session reconnects.' }
            ]
        }
    };

    function isSnakesFaqPage() {
        var path = String((window.location && window.location.pathname) || '').toLowerCase();
        if (path.indexOf('/hacks/snakes/') >= 0) return true;
        return !!(document.body && document.body.classList.contains('snakes-theme'));
    }

    function getFaqPageKey() {
        var path = String((window.location && window.location.pathname) || '').toLowerCase();
        var segments = path.split('/').filter(Boolean);
        var file = segments.length ? segments[segments.length - 1] : '';
        file = file.replace(/\.html?$/, '');

        if (segments.indexOf('lessons') !== -1 && /^lesson[1-5]$/.test(file)) {
            return file;
        }
        if (segments.indexOf('questions') !== -1) {
            return 'question_template';
        }
        if (/^square\d+$/.test(file)) {
            return 'board-square';
        }
        return file || 'default';
    }

    function getFaqContent() {
        var key = getFaqPageKey();
        return FAQ_BY_PAGE[key] || FAQ_BY_PAGE.default;
    }

    function ensureFaqStyles() {
        if (document.getElementById('snakes-faq-style')) return;
        var style = document.createElement('style');
        style.id = 'snakes-faq-style';
        style.textContent =
            '#snakes-faq-launch{position:fixed;right:20px;bottom:20px;width:52px;height:52px;border:none;border-radius:50%;cursor:pointer;' +
            'background:linear-gradient(135deg,#2f7df4,#1d4ed8);color:#fff;font-weight:700;font-size:15px;letter-spacing:.4px;' +
            'box-shadow:0 10px 30px rgba(29,78,216,.35);z-index:1600;transition:transform .2s ease,box-shadow .2s ease,filter .2s ease}' +
            '#snakes-faq-launch:hover{transform:translateY(-2px);box-shadow:0 12px 36px rgba(29,78,216,.45);filter:brightness(1.05)}' +
            '#snakes-faq-launch.has-theme-launch{bottom:90px}' +
            '#snakes-faq-panel{position:fixed;right:20px;bottom:84px;width:min(360px,calc(100vw - 24px));max-height:min(560px,calc(100vh - 120px));' +
            'display:flex;flex-direction:column;opacity:0;transform:translateY(12px) scale(.98);pointer-events:none;' +
            'background:linear-gradient(180deg,rgba(16,23,46,.98),rgba(9,14,30,.98));border:1px solid rgba(130,173,255,.35);border-radius:14px;' +
            'box-shadow:0 16px 45px rgba(3,9,30,.55);z-index:1601;transition:opacity .18s ease,transform .18s ease}' +
            '#snakes-faq-panel.has-theme-launch{bottom:154px}' +
            '#snakes-faq-panel.is-open{opacity:1;transform:translateY(0) scale(1);pointer-events:auto}' +
            '.snakes-faq-header{display:flex;align-items:flex-start;justify-content:space-between;padding:14px 14px 10px;border-bottom:1px solid rgba(130,173,255,.2)}' +
            '.snakes-faq-eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:#9eb9ff;margin-bottom:4px}' +
            '.snakes-faq-title{font-size:15px;line-height:1.3;font-weight:700;color:#eaf0ff}' +
            '.snakes-faq-close{border:none;background:transparent;color:#bcd0ff;font-size:18px;line-height:1;cursor:pointer;padding:2px 6px}' +
            '.snakes-faq-close:hover{color:#fff}' +
            '.snakes-faq-thread{padding:12px 12px 8px;overflow:auto;display:flex;flex-direction:column;gap:8px;max-height:250px}' +
            '.snakes-faq-msg{max-width:92%;padding:9px 11px;border-radius:10px;font-size:13px;line-height:1.45}' +
            '.snakes-faq-msg-bot{align-self:flex-start;background:rgba(72,120,230,.18);color:#e8f0ff;border:1px solid rgba(125,165,255,.28)}' +
            '.snakes-faq-msg-user{align-self:flex-end;background:rgba(38,77,156,.55);color:#f4f8ff;border:1px solid rgba(133,170,255,.35)}' +
            '.snakes-faq-question-wrap{padding:10px 12px 12px;border-top:1px solid rgba(130,173,255,.2);display:flex;flex-wrap:wrap;gap:8px;overflow:auto}' +
            '.snakes-faq-chip{border:1px solid rgba(130,173,255,.35);background:rgba(16,31,68,.8);color:#d8e4ff;border-radius:999px;padding:7px 10px;' +
            'font-size:12px;line-height:1.2;cursor:pointer;transition:background .2s ease,border-color .2s ease,color .2s ease}' +
            '.snakes-faq-chip:hover{background:rgba(52,98,196,.45);border-color:rgba(151,187,255,.55);color:#ffffff}' +
            '.snakes-faq-footer{padding:0 12px 12px;font-size:11px;color:#9fb1d8}' +
            '@media (max-width:640px){#snakes-faq-launch{right:14px;bottom:14px}#snakes-faq-launch.has-theme-launch{bottom:78px}' +
            '#snakes-faq-panel{right:12px;bottom:74px;width:calc(100vw - 24px)}#snakes-faq-panel.has-theme-launch{bottom:136px}}';
        document.head.appendChild(style);
    }

    function ensureFaqAssistant() {
        if (!isSnakesFaqPage()) return;
        if (!document.body) return;
        if (document.getElementById('snakes-faq-launch')) return;

        ensureFaqStyles();
        var faq = getFaqContent();

        var launchBtn = document.createElement('button');
        launchBtn.type = 'button';
        launchBtn.id = 'snakes-faq-launch';
        launchBtn.setAttribute('aria-label', 'Open FAQ assistant');
        launchBtn.setAttribute('aria-controls', 'snakes-faq-panel');
        launchBtn.setAttribute('aria-expanded', 'false');
        launchBtn.textContent = 'FAQ';

        var panel = document.createElement('div');
        panel.id = 'snakes-faq-panel';
        panel.setAttribute('role', 'dialog');
        panel.setAttribute('aria-label', 'Page FAQ assistant');
        panel.setAttribute('aria-hidden', 'true');

        var header = document.createElement('div');
        header.className = 'snakes-faq-header';

        var headingWrap = document.createElement('div');
        var eyebrow = document.createElement('div');
        eyebrow.className = 'snakes-faq-eyebrow';
        eyebrow.textContent = 'Help Assistant';
        var title = document.createElement('div');
        title.className = 'snakes-faq-title';
        title.textContent = faq.title;
        headingWrap.appendChild(eyebrow);
        headingWrap.appendChild(title);

        var closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'snakes-faq-close';
        closeBtn.setAttribute('aria-label', 'Close FAQ assistant');
        closeBtn.textContent = 'x';

        header.appendChild(headingWrap);
        header.appendChild(closeBtn);

        var thread = document.createElement('div');
        thread.className = 'snakes-faq-thread';

        var questionWrap = document.createElement('div');
        questionWrap.className = 'snakes-faq-question-wrap';

        var footer = document.createElement('div');
        footer.className = 'snakes-faq-footer';
        footer.textContent = 'Tip: questions below are specific to this page.';

        panel.appendChild(header);
        panel.appendChild(thread);
        panel.appendChild(questionWrap);
        panel.appendChild(footer);

        function appendMessage(type, text) {
            var bubble = document.createElement('div');
            bubble.className = 'snakes-faq-msg snakes-faq-msg-' + type;
            bubble.textContent = text;
            thread.appendChild(bubble);
            thread.scrollTop = thread.scrollHeight;
        }

        function openPanel() {
            panel.classList.add('is-open');
            panel.setAttribute('aria-hidden', 'false');
            launchBtn.setAttribute('aria-expanded', 'true');
        }

        function closePanel() {
            panel.classList.remove('is-open');
            panel.setAttribute('aria-hidden', 'true');
            launchBtn.setAttribute('aria-expanded', 'false');
        }

        appendMessage('bot', faq.intro);
        appendMessage('bot', 'Select a question to get a quick answer.');

        (faq.faqs || []).forEach(function (item) {
            var chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'snakes-faq-chip';
            chip.textContent = item.q;
            chip.addEventListener('click', function () {
                appendMessage('user', item.q);
                setTimeout(function () {
                    appendMessage('bot', item.a);
                }, 120);
            });
            questionWrap.appendChild(chip);
        });

        launchBtn.addEventListener('click', function () {
            if (panel.classList.contains('is-open')) {
                closePanel();
            } else {
                openPanel();
            }
        });
        closeBtn.addEventListener('click', closePanel);

        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && panel.classList.contains('is-open')) {
                closePanel();
            }
        });

        if (document.getElementById('theme-quick-launch')) {
            launchBtn.classList.add('has-theme-launch');
            panel.classList.add('has-theme-launch');
        }

        document.body.appendChild(launchBtn);
        document.body.appendChild(panel);
    }

    function initThemeSelector() {
        ensureQuickSelector();
        var initialTheme = applyTheme(getStoredTheme(), false);
        if (document.querySelector('.theme-option')) {
            bindEvents();
            updateSelectedState(initialTheme);
        }
        ensureFaqAssistant();
    }

    // Apply theme attribute immediately to reduce flash-of-unstyled-theme.
    setThemeAttribute(getStoredTheme());

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initThemeSelector);
    } else {
        initThemeSelector();
    }
})();
