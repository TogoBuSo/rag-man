(function () {
  function toggleSourceFields() {
    var select = document.getElementById("id_source_type");
    if (!select) return;

    var value = select.value;
    var rowMap = {
      FILE: [".field-upload_file"],
      URL: [".field-source_url"],
      TEXT: [".field-title", ".field-manual_text", ".field-attachments"],
    };

    var allSelectors = [
      ".field-upload_file",
      ".field-source_url",
      ".field-title",
      ".field-manual_text",
      ".field-attachments",
    ];

    allSelectors.forEach(function (sel) {
      var el = document.querySelector(sel);
      if (el) {
        el.style.display = "none";
      }
    });

    (rowMap[value] || []).forEach(function (sel) {
      var el = document.querySelector(sel);
      if (el) {
        el.style.display = "";
      }
    });

    // Also hide empty fieldsets so only the active source section is shown
    document.querySelectorAll("fieldset.module").forEach(function (fs) {
      var rows = fs.querySelectorAll(".form-row");
      if (!rows.length) return;
      var anyVisible = Array.prototype.some.call(rows, function (r) {
        return r.style.display !== "none";
      });
      fs.style.display = anyVisible ? "" : "none";
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var select = document.getElementById("id_source_type");
    if (!select) return;
    select.addEventListener("change", toggleSourceFields);
    toggleSourceFields();
  });
})();
