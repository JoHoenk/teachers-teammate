document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("figure img").forEach(function (img) {
        if (!img.src.includes(".svg")) return;
        var a = document.createElement("a");
        a.href = img.src;
        a.target = "_blank";
        a.rel = "noopener";
        a.title = "Open diagram in full size";
        img.parentNode.insertBefore(a, img);
        a.appendChild(img);
    });
});
