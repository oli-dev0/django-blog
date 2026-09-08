import {Link as BaseLink} from "django-prose-editor/editor";

const INTERNAL_KEY_ATTRIBUTE = "data-blog-internal-key";

export const Link = BaseLink.extend({
    addAttributes() {
        return {
            ...this.parent?.(),
            internalKey: {
                default: null,
                parseHTML: (element) => element.getAttribute(INTERNAL_KEY_ATTRIBUTE),
                renderHTML: (attributes) => attributes.internalKey
                    ? {[INTERNAL_KEY_ATTRIBUTE]: attributes.internalKey}
                    : {},
            },
        };
    },
});
