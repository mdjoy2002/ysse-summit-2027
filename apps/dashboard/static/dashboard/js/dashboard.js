(() => {
  const app = document.querySelector(".dashboard-app");
  const toggle = document.querySelector(".dashboard-nav-toggle");
  const sidebar = document.querySelector(".dashboard-sidebar");

  if (!app || !toggle || !sidebar) {
    return;
  }

  const closeNavigation = () => {
    app.classList.remove("nav-open");
    toggle.setAttribute("aria-expanded", "false");
  };

  toggle.addEventListener("click", () => {
    const isOpen = app.classList.toggle("nav-open");
    toggle.setAttribute("aria-expanded", String(isOpen));
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeNavigation();
      toggle.focus();
    }
  });

  sidebar.addEventListener("click", (event) => {
    if (event.target instanceof HTMLAnchorElement && window.innerWidth <= 760) {
      closeNavigation();
    }
  });

  app.addEventListener("click", (event) => {
    if (
      app.classList.contains("nav-open")
      && event.target === app
      && window.innerWidth <= 760
    ) {
      closeNavigation();
    }
  });
})();
