(function () {
  var tabs = document.querySelectorAll(".tab");
  var panels = document.querySelectorAll(".code");

  function activate(target) {
    tabs.forEach(function (tab) {
      tab.classList.toggle("active", tab.dataset.target === target);
    });
    panels.forEach(function (panel) {
      panel.classList.toggle("active", panel.dataset.panel === target);
    });
  }

  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      activate(tab.dataset.target);
    });
  });
})();
