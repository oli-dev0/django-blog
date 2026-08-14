(function () {
  "use strict";

  function setupTagRow(root) {
    const list = root.querySelector(":scope > ul");
    const items = Array.from(root.querySelectorAll("[data-blog-tag-item]"));
    const more = root.querySelector("[data-blog-tags-more]");
    const count = root.querySelector("[data-blog-tags-count]");
    const popupItems = Array.from(root.querySelectorAll(".blog-tags__popup > ul > li"));
    if (!list || !items.length || !more || !count || popupItems.length !== items.length) return;
    const details = more.querySelector("details");
    const popup = more.querySelector(".blog-tags__popup");

    function keepPopupInViewport() {
      if (!details || !popup || !details.open) return;
      popup.style.transform = "";
      const rect = popup.getBoundingClientRect();
      const viewportPadding = 16;
      let shift = 0;
      if (rect.left < viewportPadding) shift = viewportPadding - rect.left;
      if (rect.right + shift > window.innerWidth - viewportPadding) {
        shift += window.innerWidth - viewportPadding - (rect.right + shift);
      }
      popup.style.transform = `translateX(${shift}px)`;
    }

    if (details && popup) {
      details.addEventListener("toggle", () => {
        if (details.open) window.requestAnimationFrame(keepPopupInViewport);
        else popup.style.transform = "";
      });
      window.addEventListener("resize", keepPopupInViewport);
    }

    function measure() {
      items.forEach((item) => { item.hidden = false; });
      more.hidden = false;
      const styles = window.getComputedStyle(list);
      const gap = parseFloat(styles.columnGap) || parseFloat(styles.gap) || 0;
      const available = list.clientWidth;
      const widths = items.map((item) => item.getBoundingClientRect().width);
      const moreWidth = more.getBoundingClientRect().width;
      let used = 0;
      let visibleCount = widths.length;
      for (let index = 0; index < widths.length; index += 1) {
        const next = used + (index ? gap : 0) + widths[index];
        if (next > available) { visibleCount = index; break; }
        used = next;
      }
      if (visibleCount < widths.length) {
        while (visibleCount > 0 && used + gap + moreWidth > available) {
          visibleCount -= 1;
          used -= widths[visibleCount] + (visibleCount ? gap : 0);
        }
        items.slice(visibleCount).forEach((item) => { item.hidden = true; });
        popupItems.forEach((item, index) => { item.hidden = index < visibleCount; });
        count.textContent = String(widths.length - visibleCount);
      } else {
        popupItems.forEach((item) => { item.hidden = true; });
        more.hidden = true;
      }
      keepPopupInViewport();
    }

    measure();
    if (window.ResizeObserver) new ResizeObserver(measure).observe(list);
    else window.addEventListener("resize", measure);
  }

  const tagRows = document.querySelectorAll("[data-blog-tags]");
  tagRows.forEach(setupTagRow);
  document.addEventListener("click", (event) => {
    tagRows.forEach((root) => {
      const details = root.querySelector("[data-blog-tags-more] details");
      if (details && details.open && !details.contains(event.target)) details.open = false;
    });
  });
}());
