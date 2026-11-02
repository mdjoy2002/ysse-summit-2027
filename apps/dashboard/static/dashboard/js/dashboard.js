(() => {
  const app = document.querySelector(".dashboard-app");
  const toggle = document.querySelector(".dashboard-nav-toggle");
  const sidebar = document.querySelector(".dashboard-sidebar");
  const backdrop = document.querySelector(".dashboard-nav-backdrop");

  if (!app || !toggle || !sidebar || !backdrop) {
    return;
  }

  const closeNavigation = () => {
    app.classList.remove("nav-open");
    document.body.classList.remove("dashboard-menu-open");
    toggle.setAttribute("aria-expanded", "false");
    backdrop.tabIndex = -1;
  };

  toggle.addEventListener("click", () => {
    const isOpen = app.classList.toggle("nav-open");
    document.body.classList.toggle("dashboard-menu-open", isOpen);
    toggle.setAttribute("aria-expanded", String(isOpen));
    if (isOpen) {
      backdrop.tabIndex = 0;
      sidebar.querySelector("a")?.focus();
    } else {
      backdrop.tabIndex = -1;
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && app.classList.contains("nav-open")) {
      closeNavigation();
      toggle.focus();
    }
  });

  backdrop.addEventListener("click", closeNavigation);

  sidebar.addEventListener("click", (event) => {
    if (
      event.target instanceof Element
      && event.target.closest("a")
      && window.innerWidth <= 760
    ) {
      closeNavigation();
    }
  });

  window.addEventListener("resize", () => {
    if (window.innerWidth > 760 && app.classList.contains("nav-open")) {
      closeNavigation();
    }
  });
})();
