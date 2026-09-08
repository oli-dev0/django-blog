'use strict';

{
    const embedDeleteSelector = 'input[type="checkbox"][name^="blog_blogembedsharingblock_set-"][name$="-DELETE"]';

    function embedInlineIsPopulated(inline) {
        return Boolean(
            inline.querySelector('input[name$="-id"]')?.value
            || inline.querySelector('[name$="-platform"]')?.value
            || inline.querySelector('[name$="-url"]')?.value.trim()
            || inline.querySelector('[name$="-caption"]')?.value.trim()
        );
    }

    function confirmEmbedRemoval(event) {
        const target = event.target.closest?.(embedDeleteSelector);
        if (!target || !target.checked) {
            return;
        }

        const inline = target.closest('.inline-related');
        if (!inline || !embedInlineIsPopulated(inline)) {
            return;
        }

        if (window.confirm(window.gettext('Remove this embedded content from the article?'))) {
            return;
        }

        event.preventDefault();
        event.stopImmediatePropagation();
        target.checked = false;
        inline.classList.remove('collapsed', 'for-deletion');
        target.focus();
    }

    document.addEventListener('click', confirmEmbedRemoval, true);

    function internalLinkDestinations() {
        const source = document.getElementById('blog-internal-link-destinations');
        if (!source) {
            return [];
        }
        try {
            return JSON.parse(source.textContent || '[]');
        } catch (error) {
            console.error('Unable to read Blog internal-link destinations.', error);
            return [];
        }
    }

    function selectedBlogSites() {
        return new Set([...document.querySelectorAll('input[name="publication_sites"]:checked')]
            .map((input) => input.value));
    }

    function addInternalLinkButton(editor, textarea) {
        if (textarea.dataset.blogRichText !== 'true') {
            return;
        }
        const editorRoot = textarea.closest('.prose-editor') || editor.view.dom.closest('.prose-editor');
        if (!editorRoot) {
            return;
        }

        let observer;
        const addButton = () => {
            const menubar = editorRoot.querySelector('.prose-menubar:not(.prose-menubar--floating)');
            if (!menubar || menubar.querySelector('[data-blog-internal-link]')) {
                return false;
            }

            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'prose-menubar__button material-icons';
            button.dataset.blogInternalLink = 'true';
            button.setAttribute('aria-label', 'Insert internal link');
            button.title = 'Insert internal link';
            button.textContent = 'link';

            const picker = document.createElement('select');
            picker.className = 'blog-internal-link-picker';
            picker.setAttribute('aria-label', 'Internal link destination');
            picker.hidden = true;
            picker.innerHTML = '<option value="">Choose destination…</option>';

            const refreshChoices = () => {
                const sites = selectedBlogSites();
                const current = picker.value;
                picker.replaceChildren(new Option('Choose destination…', ''));
                internalLinkDestinations()
                    .filter((destination) => sites.size > 0
                        && [...sites].every((site) => destination.allowed_site_slugs.includes(site)))
                    .sort((left, right) => left.label.localeCompare(right.label))
                    .forEach((destination) => picker.add(new Option(destination.label, destination.key)));
                picker.value = [...picker.options].some((option) => option.value === current) ? current : '';
            };

            button.addEventListener('mousedown', (event) => event.preventDefault());
            button.addEventListener('click', () => {
                refreshChoices();
                picker.hidden = !picker.hidden;
                if (!picker.hidden) {
                    picker.focus();
                }
            });
            picker.addEventListener('change', () => {
                const destination = internalLinkDestinations().find((item) => item.key === picker.value);
                if (!destination || editor.state.selection.empty) {
                    return;
                }
                editor.chain().focus().setLink({
                    href: destination.url,
                    internalKey: destination.key,
                    target: null,
                    rel: null,
                }).run();
                picker.hidden = true;
                picker.value = '';
            });

            menubar.append(button, picker);
            document.querySelectorAll('input[name="publication_sites"]').forEach((input) => {
                input.addEventListener('change', refreshChoices);
            });
            return true;
        };

        if (addButton()) {
            return;
        }
        observer = new MutationObserver(() => {
            if (addButton()) {
                observer.disconnect();
            }
        });
        observer.observe(editorRoot, {childList: true, subtree: true});
        window.setTimeout(() => observer.disconnect(), 5000);
    }

    function addInlineCodeButton(editor, textarea) {
        const editorRoot = textarea.closest('.prose-editor') || editor.view.dom.closest('.prose-editor');
        if (!editorRoot) {
            return;
        }

        let observer;
        const addButton = () => {
            const menubar = editorRoot.querySelector('.prose-menubar:not(.prose-menubar--floating)');
            if (!menubar || menubar.querySelector('[data-blog-inline-code]')) {
                return false;
            }

            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'prose-menubar__button material-icons';
            button.dataset.blogInlineCode = 'true';
            button.setAttribute('aria-label', 'Inline code');
            button.setAttribute('aria-pressed', 'false');
            button.title = 'Inline code';
            button.textContent = 'code';
            button.addEventListener('click', () => {
                editor.chain().focus().toggleCode().run();
            });

            const updateButton = () => {
                const active = editor.isActive('code');
                button.classList.toggle('active', active);
                button.setAttribute('aria-pressed', String(active));
            };

            editor.on('selectionUpdate', updateButton);
            editor.on('transaction', updateButton);
            menubar.append(button);
            updateButton();
            return true;
        };

        if (addButton()) {
            return;
        }

        observer = new MutationObserver(() => {
            if (addButton()) {
                observer.disconnect();
            }
        });
        observer.observe(editorRoot, {childList: true, subtree: true});
        window.setTimeout(() => observer.disconnect(), 5000);
    }

    function setAllTags(picker, checked) {
        picker.querySelectorAll('input[type="checkbox"]').forEach((checkbox) => {
            checkbox.checked = checked;
            checkbox.dispatchEvent(new Event('change', {bubbles: true}));
        });
    }

    function addTagChoice(picker, newId, newLabel) {
        const choices = picker.querySelector('.blog-tag-picker__choices');
        const alreadyExists = [...(choices?.querySelectorAll('input[type="checkbox"]') ?? [])]
            .some((checkbox) => checkbox.value === String(newId));
        if (!choices || alreadyExists) {
            return;
        }

        const optionCount = choices.querySelectorAll('input[type="checkbox"]').length;
        const option = document.createElement('div');
        const label = document.createElement('label');
        const checkbox = document.createElement('input');

        checkbox.type = 'checkbox';
        checkbox.name = 'tags';
        checkbox.value = newId;
        checkbox.id = `${choices.id}_${optionCount}`;
        checkbox.checked = true;
        label.htmlFor = checkbox.id;
        label.append(checkbox, document.createTextNode(` ${newLabel}`));
        option.append(label);
        choices.append(option);
        checkbox.dispatchEvent(new Event('change', {bubbles: true}));
    }

    function updateComparisonPreview(select) {
        const preview = select.closest('.blog-image-comparison-picker')?.querySelector('[data-blog-comparison-preview]');
        if (!preview) {
            return;
        }

        const option = select.selectedOptions[0];
        const firstImage = preview.querySelector('[data-blog-comparison-preview-first]');
        const secondImage = preview.querySelector('[data-blog-comparison-preview-second]');
        const caption = preview.querySelector('[data-blog-comparison-preview-caption]');
        if (!option || !option.value || !option.dataset.firstPreview) {
            preview.hidden = true;
            return;
        }

        firstImage.src = option.dataset.firstPreview;
        firstImage.alt = option.dataset.firstAlt || '';
        secondImage.src = option.dataset.secondPreview;
        secondImage.alt = option.dataset.secondAlt || '';
        caption.textContent = [option.dataset.captionTitle, option.dataset.captionText]
            .filter(Boolean)
            .join(' ');
        preview.hidden = false;
    }

    function initializeComparisonPreviews(root) {
        if (root.matches?.('[data-blog-comparison-select]')) {
            updateComparisonPreview(root);
        }
        root.querySelectorAll('[data-blog-comparison-select]').forEach((select) => {
            if (select.dataset.blogComparisonInitialized) {
                return;
            }
            select.dataset.blogComparisonInitialized = 'true';
            select.addEventListener('change', () => updateComparisonPreview(select));
            updateComparisonPreview(select);
        });
    }

    function initializeImportFileNames(root) {
        root.querySelectorAll('[data-blog-import-file-field]').forEach((field) => {
            const input = field.querySelector('input[type="file"]');
            const output = field.querySelector('[data-blog-import-selected-files]');
            if (!input || !output || input.dataset.blogImportFilesInitialized) {
                return;
            }

            input.dataset.blogImportFilesInitialized = 'true';
            input.addEventListener('change', () => {
                const files = [...(input.files || [])];
                output.replaceChildren();
                files.forEach((file) => {
                    const item = document.createElement('li');
                    item.textContent = file.name;
                    output.append(item);
                });
                output.hidden = files.length === 0;
            });
        });
    }

    function initializeImportPage(root) {
        initializeImportFileNames(root);
        const errorSummary = root.querySelector('[data-blog-import-error-summary]');
        if (errorSummary) {
            errorSummary.focus();
        }
    }

    document.addEventListener('prose-editor:ready', (event) => {
        const editor = event.detail?.editor;
        const textarea = event.detail?.textarea;
        if (editor && textarea) {
            addInlineCodeButton(editor, textarea);
            addInternalLinkButton(editor, textarea);
        }
    });

    document.addEventListener('DOMContentLoaded', () => {
        initializeComparisonPreviews(document);
        document.querySelectorAll('[data-blog-import-page]').forEach(initializeImportPage);
        const comparisonObserver = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                mutation.addedNodes.forEach((node) => {
                    if (node.nodeType === Node.ELEMENT_NODE) {
                        initializeComparisonPreviews(node);
                    }
                });
            });
        });
        comparisonObserver.observe(document.body, {childList: true, subtree: true});

        document.querySelectorAll('[data-blog-tag-picker]').forEach((picker) => {
            picker.addEventListener('click', (event) => {
                const action = event.target.closest('[data-blog-tag-action]')?.dataset.blogTagAction;
                if (action === 'add-all') {
                    setAllTags(picker, true);
                } else if (action === 'remove-all') {
                    setAllTags(picker, false);
                }
            });
        });

        const dismissAddRelatedObjectPopup = window.dismissAddRelatedObjectPopup;
        window.dismissAddRelatedObjectPopup = (popup, newId, newLabel) => {
            const fieldId = popup.name.replace(/^add_/, '').replace(/__\d+$/, '');
            const picker = document.getElementById(fieldId)?.closest('[data-blog-tag-picker]');
            if (!picker) {
                dismissAddRelatedObjectPopup(popup, newId, newLabel);
                return;
            }

            addTagChoice(picker, newId, newLabel);
            const popupIndex = window.relatedWindows?.indexOf(popup) ?? -1;
            if (popupIndex >= 0) {
                window.relatedWindows.splice(popupIndex, 1);
            }
            popup.close();
        };
    });
}
