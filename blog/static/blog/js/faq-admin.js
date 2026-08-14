import {createEditor} from "django-prose-editor/configurable";

function initializeFAQ(root) {
    if (root.dataset.blogFaqInitialized) {
        return;
    }
    const valueInput = root.querySelector("[data-faq-value]");
    // Django clones its __prefix__ empty form, including data attributes.
    // Initialize only the indexed clone so it receives fresh event handlers.
    if (valueInput?.id.includes("__prefix__")) {
        return;
    }
    const itemsContainer = root.querySelector("[data-faq-items]");
    const emptyState = root.querySelector("[data-faq-empty]");
    const template = root.querySelector("[data-faq-template]");
    if (!valueInput || !itemsContainer || !emptyState || !template) {
        return;
    }
    root.dataset.blogFaqInitialized = "true";

    const items = () => [...itemsContainer.querySelectorAll(":scope > [data-faq-item]")];
    const serialize = () => {
        valueInput.value = JSON.stringify(items().map((item) => ({
            question: item.querySelector("[data-faq-question]").value,
            answer: item.querySelector("[data-faq-answer]").value,
        })));
    };
    const update = () => {
        const currentItems = items();
        const baseId = valueInput.id || `id_${valueInput.name}`;
        currentItems.forEach((item, index) => {
            const number = index + 1;
            const question = item.querySelector("[data-faq-question]");
            const answer = item.querySelector("[data-faq-answer]");
            const questionLabel = item.querySelector("[data-faq-question-label]");
            const answerLabel = item.querySelector("[data-faq-answer-label]");
            question.id = `${baseId}__question_${index}`;
            question.name = `${valueInput.name}__question_${index}`;
            answer.id = `${baseId}__answer_${index}`;
            answer.name = `${valueInput.name}__answer_${index}`;
            questionLabel.htmlFor = question.id;
            questionLabel.textContent = `${root.dataset.questionLabel} ${number}`;
            answerLabel.htmlFor = answer.id;
            answerLabel.textContent = `${root.dataset.answerLabel} ${number}`;
            item.querySelector('[data-faq-action="up"]').disabled = index === 0;
            item.querySelector('[data-faq-action="down"]').disabled = index === currentItems.length - 1;
        });
        emptyState.hidden = currentItems.length > 0;
        serialize();
    };

    root.addEventListener("input", (event) => {
        if (event.target.matches("[data-faq-question], [data-faq-answer]")) {
            serialize();
        }
    });
    root.addEventListener("change", (event) => {
        if (event.target.matches("[data-faq-question], [data-faq-answer]")) {
            serialize();
        }
    });
    root.addEventListener("prose-editor:ready", (event) => {
        event.detail?.editor?.on("transaction", serialize);
    });
    root.addEventListener("click", (event) => {
        if (event.target.closest("[data-faq-add]")) {
            const fragment = template.content.cloneNode(true);
            const item = fragment.querySelector("[data-faq-item]");
            itemsContainer.append(fragment);
            update();
            const question = item.querySelector("[data-faq-question]");
            createEditor(item.querySelector("[data-faq-answer]"));
            question.focus();
            return;
        }
        const actionButton = event.target.closest("[data-faq-action]");
        const item = actionButton?.closest("[data-faq-item]");
        if (!actionButton || !item) {
            return;
        }
        if (actionButton.dataset.faqAction === "up" && item.previousElementSibling) {
            itemsContainer.insertBefore(item, item.previousElementSibling);
        } else if (actionButton.dataset.faqAction === "down" && item.nextElementSibling) {
            itemsContainer.insertBefore(item.nextElementSibling, item);
        } else if (actionButton.dataset.faqAction === "delete") {
            const populated = item.querySelector("[data-faq-question]").value.trim()
                || item.querySelector("[data-faq-answer]").value.trim();
            if (populated && !window.confirm(root.dataset.deleteConfirmation)) {
                return;
            }
            item.remove();
        }
        update();
    });
    valueInput.closest("form")?.addEventListener("submit", serialize);
    update();
}

function initializeFAQs(root) {
    if (root.matches?.("[data-blog-faq-editor]")) {
        initializeFAQ(root);
    }
    root.querySelectorAll?.("[data-blog-faq-editor]").forEach(initializeFAQ);
}

initializeFAQs(document);
new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
        mutation.addedNodes.forEach((node) => {
            if (node.nodeType === Node.ELEMENT_NODE) {
                initializeFAQs(node);
            }
        });
    });
}).observe(document.body, {childList: true, subtree: true});
