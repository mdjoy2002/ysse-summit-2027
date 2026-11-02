(() => {
  const reducedMotion = window.matchMedia(
    "(prefers-reduced-motion: reduce)",
  ).matches;
  if (reducedMotion || !("IntersectionObserver" in window)) {
    return;
  }

  const selectors = [
    "main > section",
    "main article",
    'main [class*="-card"]',
    "main .section-heading",
    "main .about-section-heading",
    "main .hero-content",
    "main .about-hero-inner",
    "main form",
    "main .payment-panel",
    "main .dashboard-content > *",
    "footer",
  ].join(", ");
  const candidates = Array.from(document.querySelectorAll(selectors));
  const observer = new IntersectionObserver(
    (entries, currentObserver) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          currentObserver.unobserve(entry.target);
        }
      });
    },
    {
      rootMargin: "0px 0px -7% 0px",
      threshold: 0.08,
    },
  );
  let staggerIndex = 0;

  candidates.forEach((element) => {
    element.classList.add("scroll-reveal");
    element.style.setProperty(
      "--reveal-delay",
      `${(staggerIndex % 4) * 75}ms`,
    );
    staggerIndex += 1;
    observer.observe(element);
  });
})();
