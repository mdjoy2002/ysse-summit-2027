/**
 * Browser countdown for the event start time supplied by Django.
 * Dates are ISO-8601 values with a timezone offset, so client timezone is irrelevant.
 */
(function () {
  "use strict";

  function getRemainingParts(targetMilliseconds, nowMilliseconds) {
    const secondsRemaining = Math.max(
      0,
      Math.floor((targetMilliseconds - nowMilliseconds) / 1000),
    );

    return {
      days: Math.floor(secondsRemaining / 86400),
      hours: Math.floor((secondsRemaining % 86400) / 3600),
      minutes: Math.floor((secondsRemaining % 3600) / 60),
      seconds: secondsRemaining % 60,
      expired: targetMilliseconds <= nowMilliseconds,
    };
  }

  window.YSSECountdown = { getRemainingParts };

  function startCountdown() {
    const countdown = document.querySelector("[data-countdown]");
    if (!countdown) {
      return;
    }

    const targetMilliseconds = Date.parse(countdown.dataset.eventStart);
    if (!Number.isFinite(targetMilliseconds)) {
      countdown.hidden = true;
      return;
    }

    const expiredMessage = document.querySelector("[data-countdown-expired]");
    let intervalId;

    function updateCountdown() {
      const remaining = getRemainingParts(targetMilliseconds, Date.now());
      for (const [unit, value] of Object.entries(remaining)) {
        if (unit === "expired") {
          continue;
        }
        const element = countdown.querySelector(`[data-unit="${unit}"]`);
        if (element) {
          element.textContent = String(value).padStart(2, "0");
        }
      }

      if (remaining.expired) {
        if (expiredMessage) {
          expiredMessage.textContent = countdown.dataset.expiredText;
          expiredMessage.hidden = false;
        }
        if (intervalId) {
          window.clearInterval(intervalId);
        }
      }
    }

    updateCountdown();
    if (targetMilliseconds > Date.now()) {
      intervalId = window.setInterval(updateCountdown, 1000);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startCountdown, { once: true });
  } else {
    startCountdown();
  }
})();
