/* Minimal local runtime for Claude Design (.dc.html) files.
 *
 * Claude Design previews these inside its own player; exported files reference
 * ./support.js, which is not shipped. This is a small stand-in so a .dc.html
 * can be opened and clicked through locally. It implements only what the
 * exported markup actually uses:
 *
 *   <helmet>            styles hoisted into <head>
 *   <sc-if value>       conditional, unwrapped (no layout box of its own)
 *   <sc-for list as>    repeat, unwrapped
 *   {{ path }}          in text nodes and attribute values
 *   onClick/onChange    attribute bound to a function from renderVals()
 *   class X extends DCLogic { state, setState, props, renderVals() }
 *
 * Expressions are dotted paths or the literals true/false — that is all the
 * exporter emits. This is a design harness, not a framework.
 */
(function () {
  "use strict";

  var EXPR = /\{\{([^}]*)\}\}/g;

  function resolve(raw, scope) {
    var expr = raw.trim();
    if (expr === "true") return true;
    if (expr === "false") return false;
    if (expr === "") return undefined;
    if (/^-?\d+(\.\d+)?$/.test(expr)) return Number(expr);
    var parts = expr.split(".");
    var val = scope;
    for (var i = 0; i < parts.length; i++) {
      if (val == null) return undefined;
      val = val[parts[i].trim()];
    }
    return val;
  }

  // Whole-attribute single expression keeps its native type (functions,
  // booleans); anything mixed with literal text is stringified.
  function interpolate(text, scope) {
    var only = text.match(/^\s*\{\{([^}]*)\}\}\s*$/);
    if (only) return resolve(only[1], scope);
    return text.replace(EXPR, function (_, e) {
      var v = resolve(e, scope);
      return v == null ? "" : String(v);
    });
  }

  var EVENTS = { onclick: "click", onchange: "input", oninput: "input" };

  function processNode(node, scope, out) {
    if (node.nodeType === 3) {
      var t = node.nodeValue;
      out.appendChild(
        document.createTextNode(EXPR.test(t) ? String(interpolate(t, scope) ?? "") : t)
      );
      EXPR.lastIndex = 0;
      return;
    }
    if (node.nodeType !== 1) return;

    var tag = node.tagName.toLowerCase();

    if (tag === "sc-if") {
      if (interpolate(node.getAttribute("value") || "", scope)) {
        processChildren(node, scope, out); // unwrapped: no layout box
      }
      return;
    }

    if (tag === "sc-for") {
      var list = interpolate(node.getAttribute("list") || "", scope);
      var as = node.getAttribute("as") || "item";
      if (Array.isArray(list)) {
        for (var i = 0; i < list.length; i++) {
          var child = Object.create(scope);
          child[as] = list[i];
          child["$index"] = i;
          processChildren(node, child, out);
        }
      }
      return;
    }

    if (tag === "helmet" || tag === "x-dc") {
      processChildren(node, scope, out);
      return;
    }

    var el = document.createElement(tag);
    for (var a = 0; a < node.attributes.length; a++) {
      var attr = node.attributes[a];
      var name = attr.name;
      if (name.indexOf("hint-") === 0) continue; // design-tool hints

      var evt = EVENTS[name.toLowerCase()];
      if (evt) {
        var handler = interpolate(attr.value, scope);
        if (typeof handler === "function") el.addEventListener(evt, handler);
        continue;
      }

      var value = attr.value.indexOf("{{") >= 0
        ? interpolate(attr.value, scope)
        : attr.value;
      if (value == null || value === false) continue;
      if (name === "value") {
        el.value = String(value); // property, so re-render updates the field
        el.setAttribute("value", String(value));
      } else {
        el.setAttribute(name, String(value));
      }
    }
    processChildren(node, scope, el);
    out.appendChild(el);
  }

  function processChildren(node, scope, out) {
    var kids = node.childNodes;
    for (var i = 0; i < kids.length; i++) processNode(kids[i], scope, out);
  }

  function DCLogic() {}
  DCLogic.prototype.setState = function (patch) {
    var next = typeof patch === "function" ? patch(this.state) : patch;
    this.state = Object.assign({}, this.state, next);
    this.__render();
  };
  DCLogic.prototype.renderVals = function () { return {}; };
  window.DCLogic = DCLogic;

  function readProps(scriptEl) {
    var props = {};
    try {
      var spec = JSON.parse(scriptEl.getAttribute("data-props") || "{}");
      Object.keys(spec).forEach(function (k) {
        if (spec[k] && typeof spec[k] === "object" && "default" in spec[k]) {
          props[k] = spec[k]["default"];
        }
      });
    } catch (e) {
      console.warn("[support.js] could not parse data-props:", e);
    }
    return props;
  }

  function boot() {
    var template = document.querySelector("x-dc");
    var script = document.querySelector('script[type="text/x-dc"]');
    if (!template || !script) {
      console.warn("[support.js] no <x-dc> template or x-dc script found");
      return;
    }

    // Hoist <helmet> styles before first paint.
    var head = document.head;
    template.querySelectorAll("helmet style").forEach(function (s) {
      head.appendChild(s.cloneNode(true));
    });
    // Unknown elements default to display:inline and would break flex layouts;
    // we unwrap them anyway, this is belt-and-braces for anything missed.
    var reset = document.createElement("style");
    reset.textContent = "x-dc,sc-if,sc-for,helmet{display:contents}";
    head.appendChild(reset);

    var Component;
    try {
      Component = new Function("DCLogic", script.textContent + "\n;return Component;")(DCLogic);
    } catch (e) {
      console.error("[support.js] component script failed to evaluate:", e);
      return;
    }

    var instance = new Component();
    instance.props = readProps(script);
    if (!instance.state) instance.state = {};

    var mount = document.createElement("div");
    template.parentNode.insertBefore(mount, template);
    template.style.display = "none";

    instance.__render = function () {
      // Preserve focus and caret across the full re-render.
      var active = document.activeElement;
      var inputs = Array.prototype.slice.call(mount.querySelectorAll("input"));
      var idx = inputs.indexOf(active);
      var start = idx >= 0 ? active.selectionStart : null;

      var frag = document.createDocumentFragment();
      var vals;
      try {
        vals = instance.renderVals() || {};
      } catch (e) {
        console.error("[support.js] renderVals() threw:", e);
        return;
      }
      processChildren(template, vals, frag);
      mount.textContent = "";
      mount.appendChild(frag);

      if (idx >= 0) {
        var again = mount.querySelectorAll("input")[idx];
        if (again) {
          again.focus();
          if (start != null && again.type === "text") {
            try { again.setSelectionRange(start, start); } catch (e) { /* non-text input */ }
          }
        }
      }
    };

    instance.__render();
    window.addEventListener("beforeunload", function () {
      if (typeof instance.componentWillUnmount === "function") {
        instance.componentWillUnmount();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
