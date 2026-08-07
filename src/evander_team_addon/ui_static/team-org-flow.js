function Jh(t) {
  return t && t.__esModule && Object.prototype.hasOwnProperty.call(t, "default") ? t.default : t;
}
var ma = { exports: {} }, Qo = {}, ya = { exports: {} }, ke = {};
/**
 * @license React
 * react.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var Dd;
function x0() {
  if (Dd) return ke;
  Dd = 1;
  var t = Symbol.for("react.element"), r = Symbol.for("react.portal"), i = Symbol.for("react.fragment"), l = Symbol.for("react.strict_mode"), u = Symbol.for("react.profiler"), c = Symbol.for("react.provider"), f = Symbol.for("react.context"), h = Symbol.for("react.forward_ref"), p = Symbol.for("react.suspense"), m = Symbol.for("react.memo"), v = Symbol.for("react.lazy"), g = Symbol.iterator;
  function y(C) {
    return C === null || typeof C != "object" ? null : (C = g && C[g] || C["@@iterator"], typeof C == "function" ? C : null);
  }
  var x = { isMounted: function() {
    return !1;
  }, enqueueForceUpdate: function() {
  }, enqueueReplaceState: function() {
  }, enqueueSetState: function() {
  } }, S = Object.assign, M = {};
  function N(C, A, te) {
    this.props = C, this.context = A, this.refs = M, this.updater = te || x;
  }
  N.prototype.isReactComponent = {}, N.prototype.setState = function(C, A) {
    if (typeof C != "object" && typeof C != "function" && C != null) throw Error("setState(...): takes an object of state variables to update or a function which returns an object of state variables.");
    this.updater.enqueueSetState(this, C, A, "setState");
  }, N.prototype.forceUpdate = function(C) {
    this.updater.enqueueForceUpdate(this, C, "forceUpdate");
  };
  function k() {
  }
  k.prototype = N.prototype;
  function $(C, A, te) {
    this.props = C, this.context = A, this.refs = M, this.updater = te || x;
  }
  var _ = $.prototype = new k();
  _.constructor = $, S(_, N.prototype), _.isPureReactComponent = !0;
  var E = Array.isArray, D = Object.prototype.hasOwnProperty, j = { current: null }, H = { key: !0, ref: !0, __self: !0, __source: !0 };
  function b(C, A, te) {
    var re, le = {}, ue = null, ae = null;
    if (A != null) for (re in A.ref !== void 0 && (ae = A.ref), A.key !== void 0 && (ue = "" + A.key), A) D.call(A, re) && !H.hasOwnProperty(re) && (le[re] = A[re]);
    var J = arguments.length - 2;
    if (J === 1) le.children = te;
    else if (1 < J) {
      for (var ce = Array(J), xe = 0; xe < J; xe++) ce[xe] = arguments[xe + 2];
      le.children = ce;
    }
    if (C && C.defaultProps) for (re in J = C.defaultProps, J) le[re] === void 0 && (le[re] = J[re]);
    return { $$typeof: t, type: C, key: ue, ref: ae, props: le, _owner: j.current };
  }
  function K(C, A) {
    return { $$typeof: t, type: C.type, key: A, ref: C.ref, props: C.props, _owner: C._owner };
  }
  function ee(C) {
    return typeof C == "object" && C !== null && C.$$typeof === t;
  }
  function U(C) {
    var A = { "=": "=0", ":": "=2" };
    return "$" + C.replace(/[=:]/g, function(te) {
      return A[te];
    });
  }
  var G = /\/+/g;
  function Q(C, A) {
    return typeof C == "object" && C !== null && C.key != null ? U("" + C.key) : A.toString(36);
  }
  function z(C, A, te, re, le) {
    var ue = typeof C;
    (ue === "undefined" || ue === "boolean") && (C = null);
    var ae = !1;
    if (C === null) ae = !0;
    else switch (ue) {
      case "string":
      case "number":
        ae = !0;
        break;
      case "object":
        switch (C.$$typeof) {
          case t:
          case r:
            ae = !0;
        }
    }
    if (ae) return ae = C, le = le(ae), C = re === "" ? "." + Q(ae, 0) : re, E(le) ? (te = "", C != null && (te = C.replace(G, "$&/") + "/"), z(le, A, te, "", function(xe) {
      return xe;
    })) : le != null && (ee(le) && (le = K(le, te + (!le.key || ae && ae.key === le.key ? "" : ("" + le.key).replace(G, "$&/") + "/") + C)), A.push(le)), 1;
    if (ae = 0, re = re === "" ? "." : re + ":", E(C)) for (var J = 0; J < C.length; J++) {
      ue = C[J];
      var ce = re + Q(ue, J);
      ae += z(ue, A, te, ce, le);
    }
    else if (ce = y(C), typeof ce == "function") for (C = ce.call(C), J = 0; !(ue = C.next()).done; ) ue = ue.value, ce = re + Q(ue, J++), ae += z(ue, A, te, ce, le);
    else if (ue === "object") throw A = String(C), Error("Objects are not valid as a React child (found: " + (A === "[object Object]" ? "object with keys {" + Object.keys(C).join(", ") + "}" : A) + "). If you meant to render a collection of children, use an array instead.");
    return ae;
  }
  function B(C, A, te) {
    if (C == null) return C;
    var re = [], le = 0;
    return z(C, re, "", "", function(ue) {
      return A.call(te, ue, le++);
    }), re;
  }
  function F(C) {
    if (C._status === -1) {
      var A = C._result;
      A = A(), A.then(function(te) {
        (C._status === 0 || C._status === -1) && (C._status = 1, C._result = te);
      }, function(te) {
        (C._status === 0 || C._status === -1) && (C._status = 2, C._result = te);
      }), C._status === -1 && (C._status = 0, C._result = A);
    }
    if (C._status === 1) return C._result.default;
    throw C._result;
  }
  var W = { current: null }, T = { transition: null }, L = { ReactCurrentDispatcher: W, ReactCurrentBatchConfig: T, ReactCurrentOwner: j };
  function V() {
    throw Error("act(...) is not supported in production builds of React.");
  }
  return ke.Children = { map: B, forEach: function(C, A, te) {
    B(C, function() {
      A.apply(this, arguments);
    }, te);
  }, count: function(C) {
    var A = 0;
    return B(C, function() {
      A++;
    }), A;
  }, toArray: function(C) {
    return B(C, function(A) {
      return A;
    }) || [];
  }, only: function(C) {
    if (!ee(C)) throw Error("React.Children.only expected to receive a single React element child.");
    return C;
  } }, ke.Component = N, ke.Fragment = i, ke.Profiler = u, ke.PureComponent = $, ke.StrictMode = l, ke.Suspense = p, ke.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED = L, ke.act = V, ke.cloneElement = function(C, A, te) {
    if (C == null) throw Error("React.cloneElement(...): The argument must be a React element, but you passed " + C + ".");
    var re = S({}, C.props), le = C.key, ue = C.ref, ae = C._owner;
    if (A != null) {
      if (A.ref !== void 0 && (ue = A.ref, ae = j.current), A.key !== void 0 && (le = "" + A.key), C.type && C.type.defaultProps) var J = C.type.defaultProps;
      for (ce in A) D.call(A, ce) && !H.hasOwnProperty(ce) && (re[ce] = A[ce] === void 0 && J !== void 0 ? J[ce] : A[ce]);
    }
    var ce = arguments.length - 2;
    if (ce === 1) re.children = te;
    else if (1 < ce) {
      J = Array(ce);
      for (var xe = 0; xe < ce; xe++) J[xe] = arguments[xe + 2];
      re.children = J;
    }
    return { $$typeof: t, type: C.type, key: le, ref: ue, props: re, _owner: ae };
  }, ke.createContext = function(C) {
    return C = { $$typeof: f, _currentValue: C, _currentValue2: C, _threadCount: 0, Provider: null, Consumer: null, _defaultValue: null, _globalName: null }, C.Provider = { $$typeof: c, _context: C }, C.Consumer = C;
  }, ke.createElement = b, ke.createFactory = function(C) {
    var A = b.bind(null, C);
    return A.type = C, A;
  }, ke.createRef = function() {
    return { current: null };
  }, ke.forwardRef = function(C) {
    return { $$typeof: h, render: C };
  }, ke.isValidElement = ee, ke.lazy = function(C) {
    return { $$typeof: v, _payload: { _status: -1, _result: C }, _init: F };
  }, ke.memo = function(C, A) {
    return { $$typeof: m, type: C, compare: A === void 0 ? null : A };
  }, ke.startTransition = function(C) {
    var A = T.transition;
    T.transition = {};
    try {
      C();
    } finally {
      T.transition = A;
    }
  }, ke.unstable_act = V, ke.useCallback = function(C, A) {
    return W.current.useCallback(C, A);
  }, ke.useContext = function(C) {
    return W.current.useContext(C);
  }, ke.useDebugValue = function() {
  }, ke.useDeferredValue = function(C) {
    return W.current.useDeferredValue(C);
  }, ke.useEffect = function(C, A) {
    return W.current.useEffect(C, A);
  }, ke.useId = function() {
    return W.current.useId();
  }, ke.useImperativeHandle = function(C, A, te) {
    return W.current.useImperativeHandle(C, A, te);
  }, ke.useInsertionEffect = function(C, A) {
    return W.current.useInsertionEffect(C, A);
  }, ke.useLayoutEffect = function(C, A) {
    return W.current.useLayoutEffect(C, A);
  }, ke.useMemo = function(C, A) {
    return W.current.useMemo(C, A);
  }, ke.useReducer = function(C, A, te) {
    return W.current.useReducer(C, A, te);
  }, ke.useRef = function(C) {
    return W.current.useRef(C);
  }, ke.useState = function(C) {
    return W.current.useState(C);
  }, ke.useSyncExternalStore = function(C, A, te) {
    return W.current.useSyncExternalStore(C, A, te);
  }, ke.useTransition = function() {
    return W.current.useTransition();
  }, ke.version = "18.3.1", ke;
}
var jd;
function fi() {
  return jd || (jd = 1, ya.exports = x0()), ya.exports;
}
/**
 * @license React
 * react-jsx-runtime.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var Od;
function S0() {
  if (Od) return Qo;
  Od = 1;
  var t = fi(), r = Symbol.for("react.element"), i = Symbol.for("react.fragment"), l = Object.prototype.hasOwnProperty, u = t.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED.ReactCurrentOwner, c = { key: !0, ref: !0, __self: !0, __source: !0 };
  function f(h, p, m) {
    var v, g = {}, y = null, x = null;
    m !== void 0 && (y = "" + m), p.key !== void 0 && (y = "" + p.key), p.ref !== void 0 && (x = p.ref);
    for (v in p) l.call(p, v) && !c.hasOwnProperty(v) && (g[v] = p[v]);
    if (h && h.defaultProps) for (v in p = h.defaultProps, p) g[v] === void 0 && (g[v] = p[v]);
    return { $$typeof: r, type: h, key: y, ref: x, props: g, _owner: u.current };
  }
  return Qo.Fragment = i, Qo.jsx = f, Qo.jsxs = f, Qo;
}
var Fd;
function _0() {
  return Fd || (Fd = 1, ma.exports = S0()), ma.exports;
}
var I = _0(), q = fi();
const E0 = /* @__PURE__ */ Jh(q);
var js = {}, va = { exports: {} }, ct = {}, wa = { exports: {} }, xa = {};
/**
 * @license React
 * scheduler.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var Hd;
function k0() {
  return Hd || (Hd = 1, (function(t) {
    function r(T, L) {
      var V = T.length;
      T.push(L);
      e: for (; 0 < V; ) {
        var C = V - 1 >>> 1, A = T[C];
        if (0 < u(A, L)) T[C] = L, T[V] = A, V = C;
        else break e;
      }
    }
    function i(T) {
      return T.length === 0 ? null : T[0];
    }
    function l(T) {
      if (T.length === 0) return null;
      var L = T[0], V = T.pop();
      if (V !== L) {
        T[0] = V;
        e: for (var C = 0, A = T.length, te = A >>> 1; C < te; ) {
          var re = 2 * (C + 1) - 1, le = T[re], ue = re + 1, ae = T[ue];
          if (0 > u(le, V)) ue < A && 0 > u(ae, le) ? (T[C] = ae, T[ue] = V, C = ue) : (T[C] = le, T[re] = V, C = re);
          else if (ue < A && 0 > u(ae, V)) T[C] = ae, T[ue] = V, C = ue;
          else break e;
        }
      }
      return L;
    }
    function u(T, L) {
      var V = T.sortIndex - L.sortIndex;
      return V !== 0 ? V : T.id - L.id;
    }
    if (typeof performance == "object" && typeof performance.now == "function") {
      var c = performance;
      t.unstable_now = function() {
        return c.now();
      };
    } else {
      var f = Date, h = f.now();
      t.unstable_now = function() {
        return f.now() - h;
      };
    }
    var p = [], m = [], v = 1, g = null, y = 3, x = !1, S = !1, M = !1, N = typeof setTimeout == "function" ? setTimeout : null, k = typeof clearTimeout == "function" ? clearTimeout : null, $ = typeof setImmediate < "u" ? setImmediate : null;
    typeof navigator < "u" && navigator.scheduling !== void 0 && navigator.scheduling.isInputPending !== void 0 && navigator.scheduling.isInputPending.bind(navigator.scheduling);
    function _(T) {
      for (var L = i(m); L !== null; ) {
        if (L.callback === null) l(m);
        else if (L.startTime <= T) l(m), L.sortIndex = L.expirationTime, r(p, L);
        else break;
        L = i(m);
      }
    }
    function E(T) {
      if (M = !1, _(T), !S) if (i(p) !== null) S = !0, F(D);
      else {
        var L = i(m);
        L !== null && W(E, L.startTime - T);
      }
    }
    function D(T, L) {
      S = !1, M && (M = !1, k(b), b = -1), x = !0;
      var V = y;
      try {
        for (_(L), g = i(p); g !== null && (!(g.expirationTime > L) || T && !U()); ) {
          var C = g.callback;
          if (typeof C == "function") {
            g.callback = null, y = g.priorityLevel;
            var A = C(g.expirationTime <= L);
            L = t.unstable_now(), typeof A == "function" ? g.callback = A : g === i(p) && l(p), _(L);
          } else l(p);
          g = i(p);
        }
        if (g !== null) var te = !0;
        else {
          var re = i(m);
          re !== null && W(E, re.startTime - L), te = !1;
        }
        return te;
      } finally {
        g = null, y = V, x = !1;
      }
    }
    var j = !1, H = null, b = -1, K = 5, ee = -1;
    function U() {
      return !(t.unstable_now() - ee < K);
    }
    function G() {
      if (H !== null) {
        var T = t.unstable_now();
        ee = T;
        var L = !0;
        try {
          L = H(!0, T);
        } finally {
          L ? Q() : (j = !1, H = null);
        }
      } else j = !1;
    }
    var Q;
    if (typeof $ == "function") Q = function() {
      $(G);
    };
    else if (typeof MessageChannel < "u") {
      var z = new MessageChannel(), B = z.port2;
      z.port1.onmessage = G, Q = function() {
        B.postMessage(null);
      };
    } else Q = function() {
      N(G, 0);
    };
    function F(T) {
      H = T, j || (j = !0, Q());
    }
    function W(T, L) {
      b = N(function() {
        T(t.unstable_now());
      }, L);
    }
    t.unstable_IdlePriority = 5, t.unstable_ImmediatePriority = 1, t.unstable_LowPriority = 4, t.unstable_NormalPriority = 3, t.unstable_Profiling = null, t.unstable_UserBlockingPriority = 2, t.unstable_cancelCallback = function(T) {
      T.callback = null;
    }, t.unstable_continueExecution = function() {
      S || x || (S = !0, F(D));
    }, t.unstable_forceFrameRate = function(T) {
      0 > T || 125 < T ? console.error("forceFrameRate takes a positive int between 0 and 125, forcing frame rates higher than 125 fps is not supported") : K = 0 < T ? Math.floor(1e3 / T) : 5;
    }, t.unstable_getCurrentPriorityLevel = function() {
      return y;
    }, t.unstable_getFirstCallbackNode = function() {
      return i(p);
    }, t.unstable_next = function(T) {
      switch (y) {
        case 1:
        case 2:
        case 3:
          var L = 3;
          break;
        default:
          L = y;
      }
      var V = y;
      y = L;
      try {
        return T();
      } finally {
        y = V;
      }
    }, t.unstable_pauseExecution = function() {
    }, t.unstable_requestPaint = function() {
    }, t.unstable_runWithPriority = function(T, L) {
      switch (T) {
        case 1:
        case 2:
        case 3:
        case 4:
        case 5:
          break;
        default:
          T = 3;
      }
      var V = y;
      y = T;
      try {
        return L();
      } finally {
        y = V;
      }
    }, t.unstable_scheduleCallback = function(T, L, V) {
      var C = t.unstable_now();
      switch (typeof V == "object" && V !== null ? (V = V.delay, V = typeof V == "number" && 0 < V ? C + V : C) : V = C, T) {
        case 1:
          var A = -1;
          break;
        case 2:
          A = 250;
          break;
        case 5:
          A = 1073741823;
          break;
        case 4:
          A = 1e4;
          break;
        default:
          A = 5e3;
      }
      return A = V + A, T = { id: v++, callback: L, priorityLevel: T, startTime: V, expirationTime: A, sortIndex: -1 }, V > C ? (T.sortIndex = V, r(m, T), i(p) === null && T === i(m) && (M ? (k(b), b = -1) : M = !0, W(E, V - C))) : (T.sortIndex = A, r(p, T), S || x || (S = !0, F(D))), T;
    }, t.unstable_shouldYield = U, t.unstable_wrapCallback = function(T) {
      var L = y;
      return function() {
        var V = y;
        y = L;
        try {
          return T.apply(this, arguments);
        } finally {
          y = V;
        }
      };
    };
  })(xa)), xa;
}
var Vd;
function N0() {
  return Vd || (Vd = 1, wa.exports = k0()), wa.exports;
}
/**
 * @license React
 * react-dom.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var Bd;
function C0() {
  if (Bd) return ct;
  Bd = 1;
  var t = fi(), r = N0();
  function i(e) {
    for (var n = "https://reactjs.org/docs/error-decoder.html?invariant=" + e, o = 1; o < arguments.length; o++) n += "&args[]=" + encodeURIComponent(arguments[o]);
    return "Minified React error #" + e + "; visit " + n + " for the full message or use the non-minified dev environment for full errors and additional helpful warnings.";
  }
  var l = /* @__PURE__ */ new Set(), u = {};
  function c(e, n) {
    f(e, n), f(e + "Capture", n);
  }
  function f(e, n) {
    for (u[e] = n, e = 0; e < n.length; e++) l.add(n[e]);
  }
  var h = !(typeof window > "u" || typeof window.document > "u" || typeof window.document.createElement > "u"), p = Object.prototype.hasOwnProperty, m = /^[:A-Z_a-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF\u0370-\u037D\u037F-\u1FFF\u200C-\u200D\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD][:A-Z_a-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF\u0370-\u037D\u037F-\u1FFF\u200C-\u200D\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD\-.0-9\u00B7\u0300-\u036F\u203F-\u2040]*$/, v = {}, g = {};
  function y(e) {
    return p.call(g, e) ? !0 : p.call(v, e) ? !1 : m.test(e) ? g[e] = !0 : (v[e] = !0, !1);
  }
  function x(e, n, o, s) {
    if (o !== null && o.type === 0) return !1;
    switch (typeof n) {
      case "function":
      case "symbol":
        return !0;
      case "boolean":
        return s ? !1 : o !== null ? !o.acceptsBooleans : (e = e.toLowerCase().slice(0, 5), e !== "data-" && e !== "aria-");
      default:
        return !1;
    }
  }
  function S(e, n, o, s) {
    if (n === null || typeof n > "u" || x(e, n, o, s)) return !0;
    if (s) return !1;
    if (o !== null) switch (o.type) {
      case 3:
        return !n;
      case 4:
        return n === !1;
      case 5:
        return isNaN(n);
      case 6:
        return isNaN(n) || 1 > n;
    }
    return !1;
  }
  function M(e, n, o, s, a, d, w) {
    this.acceptsBooleans = n === 2 || n === 3 || n === 4, this.attributeName = s, this.attributeNamespace = a, this.mustUseProperty = o, this.propertyName = e, this.type = n, this.sanitizeURL = d, this.removeEmptyString = w;
  }
  var N = {};
  "children dangerouslySetInnerHTML defaultValue defaultChecked innerHTML suppressContentEditableWarning suppressHydrationWarning style".split(" ").forEach(function(e) {
    N[e] = new M(e, 0, !1, e, null, !1, !1);
  }), [["acceptCharset", "accept-charset"], ["className", "class"], ["htmlFor", "for"], ["httpEquiv", "http-equiv"]].forEach(function(e) {
    var n = e[0];
    N[n] = new M(n, 1, !1, e[1], null, !1, !1);
  }), ["contentEditable", "draggable", "spellCheck", "value"].forEach(function(e) {
    N[e] = new M(e, 2, !1, e.toLowerCase(), null, !1, !1);
  }), ["autoReverse", "externalResourcesRequired", "focusable", "preserveAlpha"].forEach(function(e) {
    N[e] = new M(e, 2, !1, e, null, !1, !1);
  }), "allowFullScreen async autoFocus autoPlay controls default defer disabled disablePictureInPicture disableRemotePlayback formNoValidate hidden loop noModule noValidate open playsInline readOnly required reversed scoped seamless itemScope".split(" ").forEach(function(e) {
    N[e] = new M(e, 3, !1, e.toLowerCase(), null, !1, !1);
  }), ["checked", "multiple", "muted", "selected"].forEach(function(e) {
    N[e] = new M(e, 3, !0, e, null, !1, !1);
  }), ["capture", "download"].forEach(function(e) {
    N[e] = new M(e, 4, !1, e, null, !1, !1);
  }), ["cols", "rows", "size", "span"].forEach(function(e) {
    N[e] = new M(e, 6, !1, e, null, !1, !1);
  }), ["rowSpan", "start"].forEach(function(e) {
    N[e] = new M(e, 5, !1, e.toLowerCase(), null, !1, !1);
  });
  var k = /[\-:]([a-z])/g;
  function $(e) {
    return e[1].toUpperCase();
  }
  "accent-height alignment-baseline arabic-form baseline-shift cap-height clip-path clip-rule color-interpolation color-interpolation-filters color-profile color-rendering dominant-baseline enable-background fill-opacity fill-rule flood-color flood-opacity font-family font-size font-size-adjust font-stretch font-style font-variant font-weight glyph-name glyph-orientation-horizontal glyph-orientation-vertical horiz-adv-x horiz-origin-x image-rendering letter-spacing lighting-color marker-end marker-mid marker-start overline-position overline-thickness paint-order panose-1 pointer-events rendering-intent shape-rendering stop-color stop-opacity strikethrough-position strikethrough-thickness stroke-dasharray stroke-dashoffset stroke-linecap stroke-linejoin stroke-miterlimit stroke-opacity stroke-width text-anchor text-decoration text-rendering underline-position underline-thickness unicode-bidi unicode-range units-per-em v-alphabetic v-hanging v-ideographic v-mathematical vector-effect vert-adv-y vert-origin-x vert-origin-y word-spacing writing-mode xmlns:xlink x-height".split(" ").forEach(function(e) {
    var n = e.replace(
      k,
      $
    );
    N[n] = new M(n, 1, !1, e, null, !1, !1);
  }), "xlink:actuate xlink:arcrole xlink:role xlink:show xlink:title xlink:type".split(" ").forEach(function(e) {
    var n = e.replace(k, $);
    N[n] = new M(n, 1, !1, e, "http://www.w3.org/1999/xlink", !1, !1);
  }), ["xml:base", "xml:lang", "xml:space"].forEach(function(e) {
    var n = e.replace(k, $);
    N[n] = new M(n, 1, !1, e, "http://www.w3.org/XML/1998/namespace", !1, !1);
  }), ["tabIndex", "crossOrigin"].forEach(function(e) {
    N[e] = new M(e, 1, !1, e.toLowerCase(), null, !1, !1);
  }), N.xlinkHref = new M("xlinkHref", 1, !1, "xlink:href", "http://www.w3.org/1999/xlink", !0, !1), ["src", "href", "action", "formAction"].forEach(function(e) {
    N[e] = new M(e, 1, !1, e.toLowerCase(), null, !0, !0);
  });
  function _(e, n, o, s) {
    var a = N.hasOwnProperty(n) ? N[n] : null;
    (a !== null ? a.type !== 0 : s || !(2 < n.length) || n[0] !== "o" && n[0] !== "O" || n[1] !== "n" && n[1] !== "N") && (S(n, o, a, s) && (o = null), s || a === null ? y(n) && (o === null ? e.removeAttribute(n) : e.setAttribute(n, "" + o)) : a.mustUseProperty ? e[a.propertyName] = o === null ? a.type === 3 ? !1 : "" : o : (n = a.attributeName, s = a.attributeNamespace, o === null ? e.removeAttribute(n) : (a = a.type, o = a === 3 || a === 4 && o === !0 ? "" : "" + o, s ? e.setAttributeNS(s, n, o) : e.setAttribute(n, o))));
  }
  var E = t.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED, D = Symbol.for("react.element"), j = Symbol.for("react.portal"), H = Symbol.for("react.fragment"), b = Symbol.for("react.strict_mode"), K = Symbol.for("react.profiler"), ee = Symbol.for("react.provider"), U = Symbol.for("react.context"), G = Symbol.for("react.forward_ref"), Q = Symbol.for("react.suspense"), z = Symbol.for("react.suspense_list"), B = Symbol.for("react.memo"), F = Symbol.for("react.lazy"), W = Symbol.for("react.offscreen"), T = Symbol.iterator;
  function L(e) {
    return e === null || typeof e != "object" ? null : (e = T && e[T] || e["@@iterator"], typeof e == "function" ? e : null);
  }
  var V = Object.assign, C;
  function A(e) {
    if (C === void 0) try {
      throw Error();
    } catch (o) {
      var n = o.stack.trim().match(/\n( *(at )?)/);
      C = n && n[1] || "";
    }
    return `
` + C + e;
  }
  var te = !1;
  function re(e, n) {
    if (!e || te) return "";
    te = !0;
    var o = Error.prepareStackTrace;
    Error.prepareStackTrace = void 0;
    try {
      if (n) if (n = function() {
        throw Error();
      }, Object.defineProperty(n.prototype, "props", { set: function() {
        throw Error();
      } }), typeof Reflect == "object" && Reflect.construct) {
        try {
          Reflect.construct(n, []);
        } catch (Z) {
          var s = Z;
        }
        Reflect.construct(e, [], n);
      } else {
        try {
          n.call();
        } catch (Z) {
          s = Z;
        }
        e.call(n.prototype);
      }
      else {
        try {
          throw Error();
        } catch (Z) {
          s = Z;
        }
        e();
      }
    } catch (Z) {
      if (Z && s && typeof Z.stack == "string") {
        for (var a = Z.stack.split(`
`), d = s.stack.split(`
`), w = a.length - 1, P = d.length - 1; 1 <= w && 0 <= P && a[w] !== d[P]; ) P--;
        for (; 1 <= w && 0 <= P; w--, P--) if (a[w] !== d[P]) {
          if (w !== 1 || P !== 1)
            do
              if (w--, P--, 0 > P || a[w] !== d[P]) {
                var R = `
` + a[w].replace(" at new ", " at ");
                return e.displayName && R.includes("<anonymous>") && (R = R.replace("<anonymous>", e.displayName)), R;
              }
            while (1 <= w && 0 <= P);
          break;
        }
      }
    } finally {
      te = !1, Error.prepareStackTrace = o;
    }
    return (e = e ? e.displayName || e.name : "") ? A(e) : "";
  }
  function le(e) {
    switch (e.tag) {
      case 5:
        return A(e.type);
      case 16:
        return A("Lazy");
      case 13:
        return A("Suspense");
      case 19:
        return A("SuspenseList");
      case 0:
      case 2:
      case 15:
        return e = re(e.type, !1), e;
      case 11:
        return e = re(e.type.render, !1), e;
      case 1:
        return e = re(e.type, !0), e;
      default:
        return "";
    }
  }
  function ue(e) {
    if (e == null) return null;
    if (typeof e == "function") return e.displayName || e.name || null;
    if (typeof e == "string") return e;
    switch (e) {
      case H:
        return "Fragment";
      case j:
        return "Portal";
      case K:
        return "Profiler";
      case b:
        return "StrictMode";
      case Q:
        return "Suspense";
      case z:
        return "SuspenseList";
    }
    if (typeof e == "object") switch (e.$$typeof) {
      case U:
        return (e.displayName || "Context") + ".Consumer";
      case ee:
        return (e._context.displayName || "Context") + ".Provider";
      case G:
        var n = e.render;
        return e = e.displayName, e || (e = n.displayName || n.name || "", e = e !== "" ? "ForwardRef(" + e + ")" : "ForwardRef"), e;
      case B:
        return n = e.displayName || null, n !== null ? n : ue(e.type) || "Memo";
      case F:
        n = e._payload, e = e._init;
        try {
          return ue(e(n));
        } catch {
        }
    }
    return null;
  }
  function ae(e) {
    var n = e.type;
    switch (e.tag) {
      case 24:
        return "Cache";
      case 9:
        return (n.displayName || "Context") + ".Consumer";
      case 10:
        return (n._context.displayName || "Context") + ".Provider";
      case 18:
        return "DehydratedFragment";
      case 11:
        return e = n.render, e = e.displayName || e.name || "", n.displayName || (e !== "" ? "ForwardRef(" + e + ")" : "ForwardRef");
      case 7:
        return "Fragment";
      case 5:
        return n;
      case 4:
        return "Portal";
      case 3:
        return "Root";
      case 6:
        return "Text";
      case 16:
        return ue(n);
      case 8:
        return n === b ? "StrictMode" : "Mode";
      case 22:
        return "Offscreen";
      case 12:
        return "Profiler";
      case 21:
        return "Scope";
      case 13:
        return "Suspense";
      case 19:
        return "SuspenseList";
      case 25:
        return "TracingMarker";
      case 1:
      case 0:
      case 17:
      case 2:
      case 14:
      case 15:
        if (typeof n == "function") return n.displayName || n.name || null;
        if (typeof n == "string") return n;
    }
    return null;
  }
  function J(e) {
    switch (typeof e) {
      case "boolean":
      case "number":
      case "string":
      case "undefined":
        return e;
      case "object":
        return e;
      default:
        return "";
    }
  }
  function ce(e) {
    var n = e.type;
    return (e = e.nodeName) && e.toLowerCase() === "input" && (n === "checkbox" || n === "radio");
  }
  function xe(e) {
    var n = ce(e) ? "checked" : "value", o = Object.getOwnPropertyDescriptor(e.constructor.prototype, n), s = "" + e[n];
    if (!e.hasOwnProperty(n) && typeof o < "u" && typeof o.get == "function" && typeof o.set == "function") {
      var a = o.get, d = o.set;
      return Object.defineProperty(e, n, { configurable: !0, get: function() {
        return a.call(this);
      }, set: function(w) {
        s = "" + w, d.call(this, w);
      } }), Object.defineProperty(e, n, { enumerable: o.enumerable }), { getValue: function() {
        return s;
      }, setValue: function(w) {
        s = "" + w;
      }, stopTracking: function() {
        e._valueTracker = null, delete e[n];
      } };
    }
  }
  function _e(e) {
    e._valueTracker || (e._valueTracker = xe(e));
  }
  function Se(e) {
    if (!e) return !1;
    var n = e._valueTracker;
    if (!n) return !0;
    var o = n.getValue(), s = "";
    return e && (s = ce(e) ? e.checked ? "true" : "false" : e.value), e = s, e !== o ? (n.setValue(e), !0) : !1;
  }
  function ye(e) {
    if (e = e || (typeof document < "u" ? document : void 0), typeof e > "u") return null;
    try {
      return e.activeElement || e.body;
    } catch {
      return e.body;
    }
  }
  function Ne(e, n) {
    var o = n.checked;
    return V({}, n, { defaultChecked: void 0, defaultValue: void 0, value: void 0, checked: o ?? e._wrapperState.initialChecked });
  }
  function Te(e, n) {
    var o = n.defaultValue == null ? "" : n.defaultValue, s = n.checked != null ? n.checked : n.defaultChecked;
    o = J(n.value != null ? n.value : o), e._wrapperState = { initialChecked: s, initialValue: o, controlled: n.type === "checkbox" || n.type === "radio" ? n.checked != null : n.value != null };
  }
  function Ce(e, n) {
    n = n.checked, n != null && _(e, "checked", n, !1);
  }
  function We(e, n) {
    Ce(e, n);
    var o = J(n.value), s = n.type;
    if (o != null) s === "number" ? (o === 0 && e.value === "" || e.value != o) && (e.value = "" + o) : e.value !== "" + o && (e.value = "" + o);
    else if (s === "submit" || s === "reset") {
      e.removeAttribute("value");
      return;
    }
    n.hasOwnProperty("value") ? dt(e, n.type, o) : n.hasOwnProperty("defaultValue") && dt(e, n.type, J(n.defaultValue)), n.checked == null && n.defaultChecked != null && (e.defaultChecked = !!n.defaultChecked);
  }
  function It(e, n, o) {
    if (n.hasOwnProperty("value") || n.hasOwnProperty("defaultValue")) {
      var s = n.type;
      if (!(s !== "submit" && s !== "reset" || n.value !== void 0 && n.value !== null)) return;
      n = "" + e._wrapperState.initialValue, o || n === e.value || (e.value = n), e.defaultValue = n;
    }
    o = e.name, o !== "" && (e.name = ""), e.defaultChecked = !!e._wrapperState.initialChecked, o !== "" && (e.name = o);
  }
  function dt(e, n, o) {
    (n !== "number" || ye(e.ownerDocument) !== e) && (o == null ? e.defaultValue = "" + e._wrapperState.initialValue : e.defaultValue !== "" + o && (e.defaultValue = "" + o));
  }
  var ht = Array.isArray;
  function St(e, n, o, s) {
    if (e = e.options, n) {
      n = {};
      for (var a = 0; a < o.length; a++) n["$" + o[a]] = !0;
      for (o = 0; o < e.length; o++) a = n.hasOwnProperty("$" + e[o].value), e[o].selected !== a && (e[o].selected = a), a && s && (e[o].defaultSelected = !0);
    } else {
      for (o = "" + J(o), n = null, a = 0; a < e.length; a++) {
        if (e[a].value === o) {
          e[a].selected = !0, s && (e[a].defaultSelected = !0);
          return;
        }
        n !== null || e[a].disabled || (n = e[a]);
      }
      n !== null && (n.selected = !0);
    }
  }
  function Zt(e, n) {
    if (n.dangerouslySetInnerHTML != null) throw Error(i(91));
    return V({}, n, { value: void 0, defaultValue: void 0, children: "" + e._wrapperState.initialValue });
  }
  function pn(e, n) {
    var o = n.value;
    if (o == null) {
      if (o = n.children, n = n.defaultValue, o != null) {
        if (n != null) throw Error(i(92));
        if (ht(o)) {
          if (1 < o.length) throw Error(i(93));
          o = o[0];
        }
        n = o;
      }
      n == null && (n = ""), o = n;
    }
    e._wrapperState = { initialValue: J(o) };
  }
  function pr(e, n) {
    var o = J(n.value), s = J(n.defaultValue);
    o != null && (o = "" + o, o !== e.value && (e.value = o), n.defaultValue == null && e.defaultValue !== o && (e.defaultValue = o)), s != null && (e.defaultValue = "" + s);
  }
  function Hn(e) {
    var n = e.textContent;
    n === e._wrapperState.initialValue && n !== "" && n !== null && (e.value = n);
  }
  function qt(e) {
    switch (e) {
      case "svg":
        return "http://www.w3.org/2000/svg";
      case "math":
        return "http://www.w3.org/1998/Math/MathML";
      default:
        return "http://www.w3.org/1999/xhtml";
    }
  }
  function Jt(e, n) {
    return e == null || e === "http://www.w3.org/1999/xhtml" ? qt(n) : e === "http://www.w3.org/2000/svg" && n === "foreignObject" ? "http://www.w3.org/1999/xhtml" : e;
  }
  var Vn, wi = (function(e) {
    return typeof MSApp < "u" && MSApp.execUnsafeLocalFunction ? function(n, o, s, a) {
      MSApp.execUnsafeLocalFunction(function() {
        return e(n, o, s, a);
      });
    } : e;
  })(function(e, n) {
    if (e.namespaceURI !== "http://www.w3.org/2000/svg" || "innerHTML" in e) e.innerHTML = n;
    else {
      for (Vn = Vn || document.createElement("div"), Vn.innerHTML = "<svg>" + n.valueOf().toString() + "</svg>", n = Vn.firstChild; e.firstChild; ) e.removeChild(e.firstChild);
      for (; n.firstChild; ) e.appendChild(n.firstChild);
    }
  });
  function en(e, n) {
    if (n) {
      var o = e.firstChild;
      if (o && o === e.lastChild && o.nodeType === 3) {
        o.nodeValue = n;
        return;
      }
    }
    e.textContent = n;
  }
  var Bn = {
    animationIterationCount: !0,
    aspectRatio: !0,
    borderImageOutset: !0,
    borderImageSlice: !0,
    borderImageWidth: !0,
    boxFlex: !0,
    boxFlexGroup: !0,
    boxOrdinalGroup: !0,
    columnCount: !0,
    columns: !0,
    flex: !0,
    flexGrow: !0,
    flexPositive: !0,
    flexShrink: !0,
    flexNegative: !0,
    flexOrder: !0,
    gridArea: !0,
    gridRow: !0,
    gridRowEnd: !0,
    gridRowSpan: !0,
    gridRowStart: !0,
    gridColumn: !0,
    gridColumnEnd: !0,
    gridColumnSpan: !0,
    gridColumnStart: !0,
    fontWeight: !0,
    lineClamp: !0,
    lineHeight: !0,
    opacity: !0,
    order: !0,
    orphans: !0,
    tabSize: !0,
    widows: !0,
    zIndex: !0,
    zoom: !0,
    fillOpacity: !0,
    floodOpacity: !0,
    stopOpacity: !0,
    strokeDasharray: !0,
    strokeDashoffset: !0,
    strokeMiterlimit: !0,
    strokeOpacity: !0,
    strokeWidth: !0
  }, kl = ["Webkit", "ms", "Moz", "O"];
  Object.keys(Bn).forEach(function(e) {
    kl.forEach(function(n) {
      n = n + e.charAt(0).toUpperCase() + e.substring(1), Bn[n] = Bn[e];
    });
  });
  function xi(e, n, o) {
    return n == null || typeof n == "boolean" || n === "" ? "" : o || typeof n != "number" || n === 0 || Bn.hasOwnProperty(e) && Bn[e] ? ("" + n).trim() : n + "px";
  }
  function Si(e, n) {
    e = e.style;
    for (var o in n) if (n.hasOwnProperty(o)) {
      var s = o.indexOf("--") === 0, a = xi(o, n[o], s);
      o === "float" && (o = "cssFloat"), s ? e.setProperty(o, a) : e[o] = a;
    }
  }
  var Nl = V({ menuitem: !0 }, { area: !0, base: !0, br: !0, col: !0, embed: !0, hr: !0, img: !0, input: !0, keygen: !0, link: !0, meta: !0, param: !0, source: !0, track: !0, wbr: !0 });
  function ro(e, n) {
    if (n) {
      if (Nl[e] && (n.children != null || n.dangerouslySetInnerHTML != null)) throw Error(i(137, e));
      if (n.dangerouslySetInnerHTML != null) {
        if (n.children != null) throw Error(i(60));
        if (typeof n.dangerouslySetInnerHTML != "object" || !("__html" in n.dangerouslySetInnerHTML)) throw Error(i(61));
      }
      if (n.style != null && typeof n.style != "object") throw Error(i(62));
    }
  }
  function oo(e, n) {
    if (e.indexOf("-") === -1) return typeof n.is == "string";
    switch (e) {
      case "annotation-xml":
      case "color-profile":
      case "font-face":
      case "font-face-src":
      case "font-face-uri":
      case "font-face-format":
      case "font-face-name":
      case "missing-glyph":
        return !1;
      default:
        return !0;
    }
  }
  var io = null;
  function so(e) {
    return e = e.target || e.srcElement || window, e.correspondingUseElement && (e = e.correspondingUseElement), e.nodeType === 3 ? e.parentNode : e;
  }
  var lo = null, gn = null, mn = null;
  function _i(e) {
    if (e = Ao(e)) {
      if (typeof lo != "function") throw Error(i(280));
      var n = e.stateNode;
      n && (n = qi(n), lo(e.stateNode, e.type, n));
    }
  }
  function Ei(e) {
    gn ? mn ? mn.push(e) : mn = [e] : gn = e;
  }
  function ki() {
    if (gn) {
      var e = gn, n = mn;
      if (mn = gn = null, _i(e), n) for (e = 0; e < n.length; e++) _i(n[e]);
    }
  }
  function Ni(e, n) {
    return e(n);
  }
  function Ci() {
  }
  var uo = !1;
  function Mi(e, n, o) {
    if (uo) return e(n, o);
    uo = !0;
    try {
      return Ni(e, n, o);
    } finally {
      uo = !1, (gn !== null || mn !== null) && (Ci(), ki());
    }
  }
  function Wn(e, n) {
    var o = e.stateNode;
    if (o === null) return null;
    var s = qi(o);
    if (s === null) return null;
    o = s[n];
    e: switch (n) {
      case "onClick":
      case "onClickCapture":
      case "onDoubleClick":
      case "onDoubleClickCapture":
      case "onMouseDown":
      case "onMouseDownCapture":
      case "onMouseMove":
      case "onMouseMoveCapture":
      case "onMouseUp":
      case "onMouseUpCapture":
      case "onMouseEnter":
        (s = !s.disabled) || (e = e.type, s = !(e === "button" || e === "input" || e === "select" || e === "textarea")), e = !s;
        break e;
      default:
        e = !1;
    }
    if (e) return null;
    if (o && typeof o != "function") throw Error(i(231, n, typeof o));
    return o;
  }
  var ao = !1;
  if (h) try {
    var Un = {};
    Object.defineProperty(Un, "passive", { get: function() {
      ao = !0;
    } }), window.addEventListener("test", Un, Un), window.removeEventListener("test", Un, Un);
  } catch {
    ao = !1;
  }
  function Cl(e, n, o, s, a, d, w, P, R) {
    var Z = Array.prototype.slice.call(arguments, 3);
    try {
      n.apply(o, Z);
    } catch (oe) {
      this.onError(oe);
    }
  }
  var Yn = !1, gr = null, mr = !1, co = null, Ml = { onError: function(e) {
    Yn = !0, gr = e;
  } };
  function Pl(e, n, o, s, a, d, w, P, R) {
    Yn = !1, gr = null, Cl.apply(Ml, arguments);
  }
  function Il(e, n, o, s, a, d, w, P, R) {
    if (Pl.apply(this, arguments), Yn) {
      if (Yn) {
        var Z = gr;
        Yn = !1, gr = null;
      } else throw Error(i(198));
      mr || (mr = !0, co = Z);
    }
  }
  function Vt(e) {
    var n = e, o = e;
    if (e.alternate) for (; n.return; ) n = n.return;
    else {
      e = n;
      do
        n = e, (n.flags & 4098) !== 0 && (o = n.return), e = n.return;
      while (e);
    }
    return n.tag === 3 ? o : null;
  }
  function fo(e) {
    if (e.tag === 13) {
      var n = e.memoizedState;
      if (n === null && (e = e.alternate, e !== null && (n = e.memoizedState)), n !== null) return n.dehydrated;
    }
    return null;
  }
  function ho(e) {
    if (Vt(e) !== e) throw Error(i(188));
  }
  function zl(e) {
    var n = e.alternate;
    if (!n) {
      if (n = Vt(e), n === null) throw Error(i(188));
      return n !== e ? null : e;
    }
    for (var o = e, s = n; ; ) {
      var a = o.return;
      if (a === null) break;
      var d = a.alternate;
      if (d === null) {
        if (s = a.return, s !== null) {
          o = s;
          continue;
        }
        break;
      }
      if (a.child === d.child) {
        for (d = a.child; d; ) {
          if (d === o) return ho(a), e;
          if (d === s) return ho(a), n;
          d = d.sibling;
        }
        throw Error(i(188));
      }
      if (o.return !== s.return) o = a, s = d;
      else {
        for (var w = !1, P = a.child; P; ) {
          if (P === o) {
            w = !0, o = a, s = d;
            break;
          }
          if (P === s) {
            w = !0, s = a, o = d;
            break;
          }
          P = P.sibling;
        }
        if (!w) {
          for (P = d.child; P; ) {
            if (P === o) {
              w = !0, o = d, s = a;
              break;
            }
            if (P === s) {
              w = !0, s = d, o = a;
              break;
            }
            P = P.sibling;
          }
          if (!w) throw Error(i(189));
        }
      }
      if (o.alternate !== s) throw Error(i(190));
    }
    if (o.tag !== 3) throw Error(i(188));
    return o.stateNode.current === o ? e : n;
  }
  function Pi(e) {
    return e = zl(e), e !== null ? Ii(e) : null;
  }
  function Ii(e) {
    if (e.tag === 5 || e.tag === 6) return e;
    for (e = e.child; e !== null; ) {
      var n = Ii(e);
      if (n !== null) return n;
      e = e.sibling;
    }
    return null;
  }
  var zi = r.unstable_scheduleCallback, Ti = r.unstable_cancelCallback, Tl = r.unstable_shouldYield, Li = r.unstable_requestPaint, Oe = r.unstable_now, Ll = r.unstable_getCurrentPriorityLevel, po = r.unstable_ImmediatePriority, Ai = r.unstable_UserBlockingPriority, yr = r.unstable_NormalPriority, Al = r.unstable_LowPriority, Ri = r.unstable_IdlePriority, bn = null, _t = null;
  function Rl(e) {
    if (_t && typeof _t.onCommitFiberRoot == "function") try {
      _t.onCommitFiberRoot(bn, e, void 0, (e.current.flags & 128) === 128);
    } catch {
    }
  }
  var pt = Math.clz32 ? Math.clz32 : jl, $l = Math.log, Dl = Math.LN2;
  function jl(e) {
    return e >>>= 0, e === 0 ? 32 : 31 - ($l(e) / Dl | 0) | 0;
  }
  var vr = 64, wr = 4194304;
  function Bt(e) {
    switch (e & -e) {
      case 1:
        return 1;
      case 2:
        return 2;
      case 4:
        return 4;
      case 8:
        return 8;
      case 16:
        return 16;
      case 32:
        return 32;
      case 64:
      case 128:
      case 256:
      case 512:
      case 1024:
      case 2048:
      case 4096:
      case 8192:
      case 16384:
      case 32768:
      case 65536:
      case 131072:
      case 262144:
      case 524288:
      case 1048576:
      case 2097152:
        return e & 4194240;
      case 4194304:
      case 8388608:
      case 16777216:
      case 33554432:
      case 67108864:
        return e & 130023424;
      case 134217728:
        return 134217728;
      case 268435456:
        return 268435456;
      case 536870912:
        return 536870912;
      case 1073741824:
        return 1073741824;
      default:
        return e;
    }
  }
  function xr(e, n) {
    var o = e.pendingLanes;
    if (o === 0) return 0;
    var s = 0, a = e.suspendedLanes, d = e.pingedLanes, w = o & 268435455;
    if (w !== 0) {
      var P = w & ~a;
      P !== 0 ? s = Bt(P) : (d &= w, d !== 0 && (s = Bt(d)));
    } else w = o & ~a, w !== 0 ? s = Bt(w) : d !== 0 && (s = Bt(d));
    if (s === 0) return 0;
    if (n !== 0 && n !== s && (n & a) === 0 && (a = s & -s, d = n & -n, a >= d || a === 16 && (d & 4194240) !== 0)) return n;
    if ((s & 4) !== 0 && (s |= o & 16), n = e.entangledLanes, n !== 0) for (e = e.entanglements, n &= s; 0 < n; ) o = 31 - pt(n), a = 1 << o, s |= e[o], n &= ~a;
    return s;
  }
  function $i(e, n) {
    switch (e) {
      case 1:
      case 2:
      case 4:
        return n + 250;
      case 8:
      case 16:
      case 32:
      case 64:
      case 128:
      case 256:
      case 512:
      case 1024:
      case 2048:
      case 4096:
      case 8192:
      case 16384:
      case 32768:
      case 65536:
      case 131072:
      case 262144:
      case 524288:
      case 1048576:
      case 2097152:
        return n + 5e3;
      case 4194304:
      case 8388608:
      case 16777216:
      case 33554432:
      case 67108864:
        return -1;
      case 134217728:
      case 268435456:
      case 536870912:
      case 1073741824:
        return -1;
      default:
        return -1;
    }
  }
  function Ol(e, n) {
    for (var o = e.suspendedLanes, s = e.pingedLanes, a = e.expirationTimes, d = e.pendingLanes; 0 < d; ) {
      var w = 31 - pt(d), P = 1 << w, R = a[w];
      R === -1 ? ((P & o) === 0 || (P & s) !== 0) && (a[w] = $i(P, n)) : R <= n && (e.expiredLanes |= P), d &= ~P;
    }
  }
  function go(e) {
    return e = e.pendingLanes & -1073741825, e !== 0 ? e : e & 1073741824 ? 1073741824 : 0;
  }
  function Sr() {
    var e = vr;
    return vr <<= 1, (vr & 4194240) === 0 && (vr = 64), e;
  }
  function mo(e) {
    for (var n = [], o = 0; 31 > o; o++) n.push(e);
    return n;
  }
  function Xn(e, n, o) {
    e.pendingLanes |= n, n !== 536870912 && (e.suspendedLanes = 0, e.pingedLanes = 0), e = e.eventTimes, n = 31 - pt(n), e[n] = o;
  }
  function Di(e, n) {
    var o = e.pendingLanes & ~n;
    e.pendingLanes = n, e.suspendedLanes = 0, e.pingedLanes = 0, e.expiredLanes &= n, e.mutableReadLanes &= n, e.entangledLanes &= n, n = e.entanglements;
    var s = e.eventTimes;
    for (e = e.expirationTimes; 0 < o; ) {
      var a = 31 - pt(o), d = 1 << a;
      n[a] = 0, s[a] = -1, e[a] = -1, o &= ~d;
    }
  }
  function Fl(e, n) {
    var o = e.entangledLanes |= n;
    for (e = e.entanglements; o; ) {
      var s = 31 - pt(o), a = 1 << s;
      a & n | e[s] & n && (e[s] |= n), o &= ~a;
    }
  }
  var ze = 0;
  function hc(e) {
    return e &= -e, 1 < e ? 4 < e ? (e & 268435455) !== 0 ? 16 : 536870912 : 4 : 1;
  }
  var pc, Hl, gc, mc, yc, Vl = !1, ji = [], yn = null, vn = null, wn = null, yo = /* @__PURE__ */ new Map(), vo = /* @__PURE__ */ new Map(), xn = [], Bg = "mousedown mouseup touchcancel touchend touchstart auxclick dblclick pointercancel pointerdown pointerup dragend dragstart drop compositionend compositionstart keydown keypress keyup input textInput copy cut paste click change contextmenu reset submit".split(" ");
  function vc(e, n) {
    switch (e) {
      case "focusin":
      case "focusout":
        yn = null;
        break;
      case "dragenter":
      case "dragleave":
        vn = null;
        break;
      case "mouseover":
      case "mouseout":
        wn = null;
        break;
      case "pointerover":
      case "pointerout":
        yo.delete(n.pointerId);
        break;
      case "gotpointercapture":
      case "lostpointercapture":
        vo.delete(n.pointerId);
    }
  }
  function wo(e, n, o, s, a, d) {
    return e === null || e.nativeEvent !== d ? (e = { blockedOn: n, domEventName: o, eventSystemFlags: s, nativeEvent: d, targetContainers: [a] }, n !== null && (n = Ao(n), n !== null && Hl(n)), e) : (e.eventSystemFlags |= s, n = e.targetContainers, a !== null && n.indexOf(a) === -1 && n.push(a), e);
  }
  function Wg(e, n, o, s, a) {
    switch (n) {
      case "focusin":
        return yn = wo(yn, e, n, o, s, a), !0;
      case "dragenter":
        return vn = wo(vn, e, n, o, s, a), !0;
      case "mouseover":
        return wn = wo(wn, e, n, o, s, a), !0;
      case "pointerover":
        var d = a.pointerId;
        return yo.set(d, wo(yo.get(d) || null, e, n, o, s, a)), !0;
      case "gotpointercapture":
        return d = a.pointerId, vo.set(d, wo(vo.get(d) || null, e, n, o, s, a)), !0;
    }
    return !1;
  }
  function wc(e) {
    var n = Qn(e.target);
    if (n !== null) {
      var o = Vt(n);
      if (o !== null) {
        if (n = o.tag, n === 13) {
          if (n = fo(o), n !== null) {
            e.blockedOn = n, yc(e.priority, function() {
              gc(o);
            });
            return;
          }
        } else if (n === 3 && o.stateNode.current.memoizedState.isDehydrated) {
          e.blockedOn = o.tag === 3 ? o.stateNode.containerInfo : null;
          return;
        }
      }
    }
    e.blockedOn = null;
  }
  function Oi(e) {
    if (e.blockedOn !== null) return !1;
    for (var n = e.targetContainers; 0 < n.length; ) {
      var o = Wl(e.domEventName, e.eventSystemFlags, n[0], e.nativeEvent);
      if (o === null) {
        o = e.nativeEvent;
        var s = new o.constructor(o.type, o);
        io = s, o.target.dispatchEvent(s), io = null;
      } else return n = Ao(o), n !== null && Hl(n), e.blockedOn = o, !1;
      n.shift();
    }
    return !0;
  }
  function xc(e, n, o) {
    Oi(e) && o.delete(n);
  }
  function Ug() {
    Vl = !1, yn !== null && Oi(yn) && (yn = null), vn !== null && Oi(vn) && (vn = null), wn !== null && Oi(wn) && (wn = null), yo.forEach(xc), vo.forEach(xc);
  }
  function xo(e, n) {
    e.blockedOn === n && (e.blockedOn = null, Vl || (Vl = !0, r.unstable_scheduleCallback(r.unstable_NormalPriority, Ug)));
  }
  function So(e) {
    function n(a) {
      return xo(a, e);
    }
    if (0 < ji.length) {
      xo(ji[0], e);
      for (var o = 1; o < ji.length; o++) {
        var s = ji[o];
        s.blockedOn === e && (s.blockedOn = null);
      }
    }
    for (yn !== null && xo(yn, e), vn !== null && xo(vn, e), wn !== null && xo(wn, e), yo.forEach(n), vo.forEach(n), o = 0; o < xn.length; o++) s = xn[o], s.blockedOn === e && (s.blockedOn = null);
    for (; 0 < xn.length && (o = xn[0], o.blockedOn === null); ) wc(o), o.blockedOn === null && xn.shift();
  }
  var _r = E.ReactCurrentBatchConfig, Fi = !0;
  function Yg(e, n, o, s) {
    var a = ze, d = _r.transition;
    _r.transition = null;
    try {
      ze = 1, Bl(e, n, o, s);
    } finally {
      ze = a, _r.transition = d;
    }
  }
  function bg(e, n, o, s) {
    var a = ze, d = _r.transition;
    _r.transition = null;
    try {
      ze = 4, Bl(e, n, o, s);
    } finally {
      ze = a, _r.transition = d;
    }
  }
  function Bl(e, n, o, s) {
    if (Fi) {
      var a = Wl(e, n, o, s);
      if (a === null) su(e, n, s, Hi, o), vc(e, s);
      else if (Wg(a, e, n, o, s)) s.stopPropagation();
      else if (vc(e, s), n & 4 && -1 < Bg.indexOf(e)) {
        for (; a !== null; ) {
          var d = Ao(a);
          if (d !== null && pc(d), d = Wl(e, n, o, s), d === null && su(e, n, s, Hi, o), d === a) break;
          a = d;
        }
        a !== null && s.stopPropagation();
      } else su(e, n, s, null, o);
    }
  }
  var Hi = null;
  function Wl(e, n, o, s) {
    if (Hi = null, e = so(s), e = Qn(e), e !== null) if (n = Vt(e), n === null) e = null;
    else if (o = n.tag, o === 13) {
      if (e = fo(n), e !== null) return e;
      e = null;
    } else if (o === 3) {
      if (n.stateNode.current.memoizedState.isDehydrated) return n.tag === 3 ? n.stateNode.containerInfo : null;
      e = null;
    } else n !== e && (e = null);
    return Hi = e, null;
  }
  function Sc(e) {
    switch (e) {
      case "cancel":
      case "click":
      case "close":
      case "contextmenu":
      case "copy":
      case "cut":
      case "auxclick":
      case "dblclick":
      case "dragend":
      case "dragstart":
      case "drop":
      case "focusin":
      case "focusout":
      case "input":
      case "invalid":
      case "keydown":
      case "keypress":
      case "keyup":
      case "mousedown":
      case "mouseup":
      case "paste":
      case "pause":
      case "play":
      case "pointercancel":
      case "pointerdown":
      case "pointerup":
      case "ratechange":
      case "reset":
      case "resize":
      case "seeked":
      case "submit":
      case "touchcancel":
      case "touchend":
      case "touchstart":
      case "volumechange":
      case "change":
      case "selectionchange":
      case "textInput":
      case "compositionstart":
      case "compositionend":
      case "compositionupdate":
      case "beforeblur":
      case "afterblur":
      case "beforeinput":
      case "blur":
      case "fullscreenchange":
      case "focus":
      case "hashchange":
      case "popstate":
      case "select":
      case "selectstart":
        return 1;
      case "drag":
      case "dragenter":
      case "dragexit":
      case "dragleave":
      case "dragover":
      case "mousemove":
      case "mouseout":
      case "mouseover":
      case "pointermove":
      case "pointerout":
      case "pointerover":
      case "scroll":
      case "toggle":
      case "touchmove":
      case "wheel":
      case "mouseenter":
      case "mouseleave":
      case "pointerenter":
      case "pointerleave":
        return 4;
      case "message":
        switch (Ll()) {
          case po:
            return 1;
          case Ai:
            return 4;
          case yr:
          case Al:
            return 16;
          case Ri:
            return 536870912;
          default:
            return 16;
        }
      default:
        return 16;
    }
  }
  var Sn = null, Ul = null, Vi = null;
  function _c() {
    if (Vi) return Vi;
    var e, n = Ul, o = n.length, s, a = "value" in Sn ? Sn.value : Sn.textContent, d = a.length;
    for (e = 0; e < o && n[e] === a[e]; e++) ;
    var w = o - e;
    for (s = 1; s <= w && n[o - s] === a[d - s]; s++) ;
    return Vi = a.slice(e, 1 < s ? 1 - s : void 0);
  }
  function Bi(e) {
    var n = e.keyCode;
    return "charCode" in e ? (e = e.charCode, e === 0 && n === 13 && (e = 13)) : e = n, e === 10 && (e = 13), 32 <= e || e === 13 ? e : 0;
  }
  function Wi() {
    return !0;
  }
  function Ec() {
    return !1;
  }
  function gt(e) {
    function n(o, s, a, d, w) {
      this._reactName = o, this._targetInst = a, this.type = s, this.nativeEvent = d, this.target = w, this.currentTarget = null;
      for (var P in e) e.hasOwnProperty(P) && (o = e[P], this[P] = o ? o(d) : d[P]);
      return this.isDefaultPrevented = (d.defaultPrevented != null ? d.defaultPrevented : d.returnValue === !1) ? Wi : Ec, this.isPropagationStopped = Ec, this;
    }
    return V(n.prototype, { preventDefault: function() {
      this.defaultPrevented = !0;
      var o = this.nativeEvent;
      o && (o.preventDefault ? o.preventDefault() : typeof o.returnValue != "unknown" && (o.returnValue = !1), this.isDefaultPrevented = Wi);
    }, stopPropagation: function() {
      var o = this.nativeEvent;
      o && (o.stopPropagation ? o.stopPropagation() : typeof o.cancelBubble != "unknown" && (o.cancelBubble = !0), this.isPropagationStopped = Wi);
    }, persist: function() {
    }, isPersistent: Wi }), n;
  }
  var Er = { eventPhase: 0, bubbles: 0, cancelable: 0, timeStamp: function(e) {
    return e.timeStamp || Date.now();
  }, defaultPrevented: 0, isTrusted: 0 }, Yl = gt(Er), _o = V({}, Er, { view: 0, detail: 0 }), Xg = gt(_o), bl, Xl, Eo, Ui = V({}, _o, { screenX: 0, screenY: 0, clientX: 0, clientY: 0, pageX: 0, pageY: 0, ctrlKey: 0, shiftKey: 0, altKey: 0, metaKey: 0, getModifierState: Kl, button: 0, buttons: 0, relatedTarget: function(e) {
    return e.relatedTarget === void 0 ? e.fromElement === e.srcElement ? e.toElement : e.fromElement : e.relatedTarget;
  }, movementX: function(e) {
    return "movementX" in e ? e.movementX : (e !== Eo && (Eo && e.type === "mousemove" ? (bl = e.screenX - Eo.screenX, Xl = e.screenY - Eo.screenY) : Xl = bl = 0, Eo = e), bl);
  }, movementY: function(e) {
    return "movementY" in e ? e.movementY : Xl;
  } }), kc = gt(Ui), Qg = V({}, Ui, { dataTransfer: 0 }), Kg = gt(Qg), Gg = V({}, _o, { relatedTarget: 0 }), Ql = gt(Gg), Zg = V({}, Er, { animationName: 0, elapsedTime: 0, pseudoElement: 0 }), qg = gt(Zg), Jg = V({}, Er, { clipboardData: function(e) {
    return "clipboardData" in e ? e.clipboardData : window.clipboardData;
  } }), em = gt(Jg), tm = V({}, Er, { data: 0 }), Nc = gt(tm), nm = {
    Esc: "Escape",
    Spacebar: " ",
    Left: "ArrowLeft",
    Up: "ArrowUp",
    Right: "ArrowRight",
    Down: "ArrowDown",
    Del: "Delete",
    Win: "OS",
    Menu: "ContextMenu",
    Apps: "ContextMenu",
    Scroll: "ScrollLock",
    MozPrintableKey: "Unidentified"
  }, rm = {
    8: "Backspace",
    9: "Tab",
    12: "Clear",
    13: "Enter",
    16: "Shift",
    17: "Control",
    18: "Alt",
    19: "Pause",
    20: "CapsLock",
    27: "Escape",
    32: " ",
    33: "PageUp",
    34: "PageDown",
    35: "End",
    36: "Home",
    37: "ArrowLeft",
    38: "ArrowUp",
    39: "ArrowRight",
    40: "ArrowDown",
    45: "Insert",
    46: "Delete",
    112: "F1",
    113: "F2",
    114: "F3",
    115: "F4",
    116: "F5",
    117: "F6",
    118: "F7",
    119: "F8",
    120: "F9",
    121: "F10",
    122: "F11",
    123: "F12",
    144: "NumLock",
    145: "ScrollLock",
    224: "Meta"
  }, om = { Alt: "altKey", Control: "ctrlKey", Meta: "metaKey", Shift: "shiftKey" };
  function im(e) {
    var n = this.nativeEvent;
    return n.getModifierState ? n.getModifierState(e) : (e = om[e]) ? !!n[e] : !1;
  }
  function Kl() {
    return im;
  }
  var sm = V({}, _o, { key: function(e) {
    if (e.key) {
      var n = nm[e.key] || e.key;
      if (n !== "Unidentified") return n;
    }
    return e.type === "keypress" ? (e = Bi(e), e === 13 ? "Enter" : String.fromCharCode(e)) : e.type === "keydown" || e.type === "keyup" ? rm[e.keyCode] || "Unidentified" : "";
  }, code: 0, location: 0, ctrlKey: 0, shiftKey: 0, altKey: 0, metaKey: 0, repeat: 0, locale: 0, getModifierState: Kl, charCode: function(e) {
    return e.type === "keypress" ? Bi(e) : 0;
  }, keyCode: function(e) {
    return e.type === "keydown" || e.type === "keyup" ? e.keyCode : 0;
  }, which: function(e) {
    return e.type === "keypress" ? Bi(e) : e.type === "keydown" || e.type === "keyup" ? e.keyCode : 0;
  } }), lm = gt(sm), um = V({}, Ui, { pointerId: 0, width: 0, height: 0, pressure: 0, tangentialPressure: 0, tiltX: 0, tiltY: 0, twist: 0, pointerType: 0, isPrimary: 0 }), Cc = gt(um), am = V({}, _o, { touches: 0, targetTouches: 0, changedTouches: 0, altKey: 0, metaKey: 0, ctrlKey: 0, shiftKey: 0, getModifierState: Kl }), cm = gt(am), fm = V({}, Er, { propertyName: 0, elapsedTime: 0, pseudoElement: 0 }), dm = gt(fm), hm = V({}, Ui, {
    deltaX: function(e) {
      return "deltaX" in e ? e.deltaX : "wheelDeltaX" in e ? -e.wheelDeltaX : 0;
    },
    deltaY: function(e) {
      return "deltaY" in e ? e.deltaY : "wheelDeltaY" in e ? -e.wheelDeltaY : "wheelDelta" in e ? -e.wheelDelta : 0;
    },
    deltaZ: 0,
    deltaMode: 0
  }), pm = gt(hm), gm = [9, 13, 27, 32], Gl = h && "CompositionEvent" in window, ko = null;
  h && "documentMode" in document && (ko = document.documentMode);
  var mm = h && "TextEvent" in window && !ko, Mc = h && (!Gl || ko && 8 < ko && 11 >= ko), Pc = " ", Ic = !1;
  function zc(e, n) {
    switch (e) {
      case "keyup":
        return gm.indexOf(n.keyCode) !== -1;
      case "keydown":
        return n.keyCode !== 229;
      case "keypress":
      case "mousedown":
      case "focusout":
        return !0;
      default:
        return !1;
    }
  }
  function Tc(e) {
    return e = e.detail, typeof e == "object" && "data" in e ? e.data : null;
  }
  var kr = !1;
  function ym(e, n) {
    switch (e) {
      case "compositionend":
        return Tc(n);
      case "keypress":
        return n.which !== 32 ? null : (Ic = !0, Pc);
      case "textInput":
        return e = n.data, e === Pc && Ic ? null : e;
      default:
        return null;
    }
  }
  function vm(e, n) {
    if (kr) return e === "compositionend" || !Gl && zc(e, n) ? (e = _c(), Vi = Ul = Sn = null, kr = !1, e) : null;
    switch (e) {
      case "paste":
        return null;
      case "keypress":
        if (!(n.ctrlKey || n.altKey || n.metaKey) || n.ctrlKey && n.altKey) {
          if (n.char && 1 < n.char.length) return n.char;
          if (n.which) return String.fromCharCode(n.which);
        }
        return null;
      case "compositionend":
        return Mc && n.locale !== "ko" ? null : n.data;
      default:
        return null;
    }
  }
  var wm = { color: !0, date: !0, datetime: !0, "datetime-local": !0, email: !0, month: !0, number: !0, password: !0, range: !0, search: !0, tel: !0, text: !0, time: !0, url: !0, week: !0 };
  function Lc(e) {
    var n = e && e.nodeName && e.nodeName.toLowerCase();
    return n === "input" ? !!wm[e.type] : n === "textarea";
  }
  function Ac(e, n, o, s) {
    Ei(s), n = Ki(n, "onChange"), 0 < n.length && (o = new Yl("onChange", "change", null, o, s), e.push({ event: o, listeners: n }));
  }
  var No = null, Co = null;
  function xm(e) {
    Zc(e, 0);
  }
  function Yi(e) {
    var n = Ir(e);
    if (Se(n)) return e;
  }
  function Sm(e, n) {
    if (e === "change") return n;
  }
  var Rc = !1;
  if (h) {
    var Zl;
    if (h) {
      var ql = "oninput" in document;
      if (!ql) {
        var $c = document.createElement("div");
        $c.setAttribute("oninput", "return;"), ql = typeof $c.oninput == "function";
      }
      Zl = ql;
    } else Zl = !1;
    Rc = Zl && (!document.documentMode || 9 < document.documentMode);
  }
  function Dc() {
    No && (No.detachEvent("onpropertychange", jc), Co = No = null);
  }
  function jc(e) {
    if (e.propertyName === "value" && Yi(Co)) {
      var n = [];
      Ac(n, Co, e, so(e)), Mi(xm, n);
    }
  }
  function _m(e, n, o) {
    e === "focusin" ? (Dc(), No = n, Co = o, No.attachEvent("onpropertychange", jc)) : e === "focusout" && Dc();
  }
  function Em(e) {
    if (e === "selectionchange" || e === "keyup" || e === "keydown") return Yi(Co);
  }
  function km(e, n) {
    if (e === "click") return Yi(n);
  }
  function Nm(e, n) {
    if (e === "input" || e === "change") return Yi(n);
  }
  function Cm(e, n) {
    return e === n && (e !== 0 || 1 / e === 1 / n) || e !== e && n !== n;
  }
  var zt = typeof Object.is == "function" ? Object.is : Cm;
  function Mo(e, n) {
    if (zt(e, n)) return !0;
    if (typeof e != "object" || e === null || typeof n != "object" || n === null) return !1;
    var o = Object.keys(e), s = Object.keys(n);
    if (o.length !== s.length) return !1;
    for (s = 0; s < o.length; s++) {
      var a = o[s];
      if (!p.call(n, a) || !zt(e[a], n[a])) return !1;
    }
    return !0;
  }
  function Oc(e) {
    for (; e && e.firstChild; ) e = e.firstChild;
    return e;
  }
  function Fc(e, n) {
    var o = Oc(e);
    e = 0;
    for (var s; o; ) {
      if (o.nodeType === 3) {
        if (s = e + o.textContent.length, e <= n && s >= n) return { node: o, offset: n - e };
        e = s;
      }
      e: {
        for (; o; ) {
          if (o.nextSibling) {
            o = o.nextSibling;
            break e;
          }
          o = o.parentNode;
        }
        o = void 0;
      }
      o = Oc(o);
    }
  }
  function Hc(e, n) {
    return e && n ? e === n ? !0 : e && e.nodeType === 3 ? !1 : n && n.nodeType === 3 ? Hc(e, n.parentNode) : "contains" in e ? e.contains(n) : e.compareDocumentPosition ? !!(e.compareDocumentPosition(n) & 16) : !1 : !1;
  }
  function Vc() {
    for (var e = window, n = ye(); n instanceof e.HTMLIFrameElement; ) {
      try {
        var o = typeof n.contentWindow.location.href == "string";
      } catch {
        o = !1;
      }
      if (o) e = n.contentWindow;
      else break;
      n = ye(e.document);
    }
    return n;
  }
  function Jl(e) {
    var n = e && e.nodeName && e.nodeName.toLowerCase();
    return n && (n === "input" && (e.type === "text" || e.type === "search" || e.type === "tel" || e.type === "url" || e.type === "password") || n === "textarea" || e.contentEditable === "true");
  }
  function Mm(e) {
    var n = Vc(), o = e.focusedElem, s = e.selectionRange;
    if (n !== o && o && o.ownerDocument && Hc(o.ownerDocument.documentElement, o)) {
      if (s !== null && Jl(o)) {
        if (n = s.start, e = s.end, e === void 0 && (e = n), "selectionStart" in o) o.selectionStart = n, o.selectionEnd = Math.min(e, o.value.length);
        else if (e = (n = o.ownerDocument || document) && n.defaultView || window, e.getSelection) {
          e = e.getSelection();
          var a = o.textContent.length, d = Math.min(s.start, a);
          s = s.end === void 0 ? d : Math.min(s.end, a), !e.extend && d > s && (a = s, s = d, d = a), a = Fc(o, d);
          var w = Fc(
            o,
            s
          );
          a && w && (e.rangeCount !== 1 || e.anchorNode !== a.node || e.anchorOffset !== a.offset || e.focusNode !== w.node || e.focusOffset !== w.offset) && (n = n.createRange(), n.setStart(a.node, a.offset), e.removeAllRanges(), d > s ? (e.addRange(n), e.extend(w.node, w.offset)) : (n.setEnd(w.node, w.offset), e.addRange(n)));
        }
      }
      for (n = [], e = o; e = e.parentNode; ) e.nodeType === 1 && n.push({ element: e, left: e.scrollLeft, top: e.scrollTop });
      for (typeof o.focus == "function" && o.focus(), o = 0; o < n.length; o++) e = n[o], e.element.scrollLeft = e.left, e.element.scrollTop = e.top;
    }
  }
  var Pm = h && "documentMode" in document && 11 >= document.documentMode, Nr = null, eu = null, Po = null, tu = !1;
  function Bc(e, n, o) {
    var s = o.window === o ? o.document : o.nodeType === 9 ? o : o.ownerDocument;
    tu || Nr == null || Nr !== ye(s) || (s = Nr, "selectionStart" in s && Jl(s) ? s = { start: s.selectionStart, end: s.selectionEnd } : (s = (s.ownerDocument && s.ownerDocument.defaultView || window).getSelection(), s = { anchorNode: s.anchorNode, anchorOffset: s.anchorOffset, focusNode: s.focusNode, focusOffset: s.focusOffset }), Po && Mo(Po, s) || (Po = s, s = Ki(eu, "onSelect"), 0 < s.length && (n = new Yl("onSelect", "select", null, n, o), e.push({ event: n, listeners: s }), n.target = Nr)));
  }
  function bi(e, n) {
    var o = {};
    return o[e.toLowerCase()] = n.toLowerCase(), o["Webkit" + e] = "webkit" + n, o["Moz" + e] = "moz" + n, o;
  }
  var Cr = { animationend: bi("Animation", "AnimationEnd"), animationiteration: bi("Animation", "AnimationIteration"), animationstart: bi("Animation", "AnimationStart"), transitionend: bi("Transition", "TransitionEnd") }, nu = {}, Wc = {};
  h && (Wc = document.createElement("div").style, "AnimationEvent" in window || (delete Cr.animationend.animation, delete Cr.animationiteration.animation, delete Cr.animationstart.animation), "TransitionEvent" in window || delete Cr.transitionend.transition);
  function Xi(e) {
    if (nu[e]) return nu[e];
    if (!Cr[e]) return e;
    var n = Cr[e], o;
    for (o in n) if (n.hasOwnProperty(o) && o in Wc) return nu[e] = n[o];
    return e;
  }
  var Uc = Xi("animationend"), Yc = Xi("animationiteration"), bc = Xi("animationstart"), Xc = Xi("transitionend"), Qc = /* @__PURE__ */ new Map(), Kc = "abort auxClick cancel canPlay canPlayThrough click close contextMenu copy cut drag dragEnd dragEnter dragExit dragLeave dragOver dragStart drop durationChange emptied encrypted ended error gotPointerCapture input invalid keyDown keyPress keyUp load loadedData loadedMetadata loadStart lostPointerCapture mouseDown mouseMove mouseOut mouseOver mouseUp paste pause play playing pointerCancel pointerDown pointerMove pointerOut pointerOver pointerUp progress rateChange reset resize seeked seeking stalled submit suspend timeUpdate touchCancel touchEnd touchStart volumeChange scroll toggle touchMove waiting wheel".split(" ");
  function _n(e, n) {
    Qc.set(e, n), c(n, [e]);
  }
  for (var ru = 0; ru < Kc.length; ru++) {
    var ou = Kc[ru], Im = ou.toLowerCase(), zm = ou[0].toUpperCase() + ou.slice(1);
    _n(Im, "on" + zm);
  }
  _n(Uc, "onAnimationEnd"), _n(Yc, "onAnimationIteration"), _n(bc, "onAnimationStart"), _n("dblclick", "onDoubleClick"), _n("focusin", "onFocus"), _n("focusout", "onBlur"), _n(Xc, "onTransitionEnd"), f("onMouseEnter", ["mouseout", "mouseover"]), f("onMouseLeave", ["mouseout", "mouseover"]), f("onPointerEnter", ["pointerout", "pointerover"]), f("onPointerLeave", ["pointerout", "pointerover"]), c("onChange", "change click focusin focusout input keydown keyup selectionchange".split(" ")), c("onSelect", "focusout contextmenu dragend focusin keydown keyup mousedown mouseup selectionchange".split(" ")), c("onBeforeInput", ["compositionend", "keypress", "textInput", "paste"]), c("onCompositionEnd", "compositionend focusout keydown keypress keyup mousedown".split(" ")), c("onCompositionStart", "compositionstart focusout keydown keypress keyup mousedown".split(" ")), c("onCompositionUpdate", "compositionupdate focusout keydown keypress keyup mousedown".split(" "));
  var Io = "abort canplay canplaythrough durationchange emptied encrypted ended error loadeddata loadedmetadata loadstart pause play playing progress ratechange resize seeked seeking stalled suspend timeupdate volumechange waiting".split(" "), Tm = new Set("cancel close invalid load scroll toggle".split(" ").concat(Io));
  function Gc(e, n, o) {
    var s = e.type || "unknown-event";
    e.currentTarget = o, Il(s, n, void 0, e), e.currentTarget = null;
  }
  function Zc(e, n) {
    n = (n & 4) !== 0;
    for (var o = 0; o < e.length; o++) {
      var s = e[o], a = s.event;
      s = s.listeners;
      e: {
        var d = void 0;
        if (n) for (var w = s.length - 1; 0 <= w; w--) {
          var P = s[w], R = P.instance, Z = P.currentTarget;
          if (P = P.listener, R !== d && a.isPropagationStopped()) break e;
          Gc(a, P, Z), d = R;
        }
        else for (w = 0; w < s.length; w++) {
          if (P = s[w], R = P.instance, Z = P.currentTarget, P = P.listener, R !== d && a.isPropagationStopped()) break e;
          Gc(a, P, Z), d = R;
        }
      }
    }
    if (mr) throw e = co, mr = !1, co = null, e;
  }
  function Ae(e, n) {
    var o = n[du];
    o === void 0 && (o = n[du] = /* @__PURE__ */ new Set());
    var s = e + "__bubble";
    o.has(s) || (qc(n, e, 2, !1), o.add(s));
  }
  function iu(e, n, o) {
    var s = 0;
    n && (s |= 4), qc(o, e, s, n);
  }
  var Qi = "_reactListening" + Math.random().toString(36).slice(2);
  function zo(e) {
    if (!e[Qi]) {
      e[Qi] = !0, l.forEach(function(o) {
        o !== "selectionchange" && (Tm.has(o) || iu(o, !1, e), iu(o, !0, e));
      });
      var n = e.nodeType === 9 ? e : e.ownerDocument;
      n === null || n[Qi] || (n[Qi] = !0, iu("selectionchange", !1, n));
    }
  }
  function qc(e, n, o, s) {
    switch (Sc(n)) {
      case 1:
        var a = Yg;
        break;
      case 4:
        a = bg;
        break;
      default:
        a = Bl;
    }
    o = a.bind(null, n, o, e), a = void 0, !ao || n !== "touchstart" && n !== "touchmove" && n !== "wheel" || (a = !0), s ? a !== void 0 ? e.addEventListener(n, o, { capture: !0, passive: a }) : e.addEventListener(n, o, !0) : a !== void 0 ? e.addEventListener(n, o, { passive: a }) : e.addEventListener(n, o, !1);
  }
  function su(e, n, o, s, a) {
    var d = s;
    if ((n & 1) === 0 && (n & 2) === 0 && s !== null) e: for (; ; ) {
      if (s === null) return;
      var w = s.tag;
      if (w === 3 || w === 4) {
        var P = s.stateNode.containerInfo;
        if (P === a || P.nodeType === 8 && P.parentNode === a) break;
        if (w === 4) for (w = s.return; w !== null; ) {
          var R = w.tag;
          if ((R === 3 || R === 4) && (R = w.stateNode.containerInfo, R === a || R.nodeType === 8 && R.parentNode === a)) return;
          w = w.return;
        }
        for (; P !== null; ) {
          if (w = Qn(P), w === null) return;
          if (R = w.tag, R === 5 || R === 6) {
            s = d = w;
            continue e;
          }
          P = P.parentNode;
        }
      }
      s = s.return;
    }
    Mi(function() {
      var Z = d, oe = so(o), ie = [];
      e: {
        var ne = Qc.get(e);
        if (ne !== void 0) {
          var fe = Yl, he = e;
          switch (e) {
            case "keypress":
              if (Bi(o) === 0) break e;
            case "keydown":
            case "keyup":
              fe = lm;
              break;
            case "focusin":
              he = "focus", fe = Ql;
              break;
            case "focusout":
              he = "blur", fe = Ql;
              break;
            case "beforeblur":
            case "afterblur":
              fe = Ql;
              break;
            case "click":
              if (o.button === 2) break e;
            case "auxclick":
            case "dblclick":
            case "mousedown":
            case "mousemove":
            case "mouseup":
            case "mouseout":
            case "mouseover":
            case "contextmenu":
              fe = kc;
              break;
            case "drag":
            case "dragend":
            case "dragenter":
            case "dragexit":
            case "dragleave":
            case "dragover":
            case "dragstart":
            case "drop":
              fe = Kg;
              break;
            case "touchcancel":
            case "touchend":
            case "touchmove":
            case "touchstart":
              fe = cm;
              break;
            case Uc:
            case Yc:
            case bc:
              fe = qg;
              break;
            case Xc:
              fe = dm;
              break;
            case "scroll":
              fe = Xg;
              break;
            case "wheel":
              fe = pm;
              break;
            case "copy":
            case "cut":
            case "paste":
              fe = em;
              break;
            case "gotpointercapture":
            case "lostpointercapture":
            case "pointercancel":
            case "pointerdown":
            case "pointermove":
            case "pointerout":
            case "pointerover":
            case "pointerup":
              fe = Cc;
          }
          var pe = (n & 4) !== 0, Be = !pe && e === "scroll", Y = pe ? ne !== null ? ne + "Capture" : null : ne;
          pe = [];
          for (var O = Z, X; O !== null; ) {
            X = O;
            var se = X.stateNode;
            if (X.tag === 5 && se !== null && (X = se, Y !== null && (se = Wn(O, Y), se != null && pe.push(To(O, se, X)))), Be) break;
            O = O.return;
          }
          0 < pe.length && (ne = new fe(ne, he, null, o, oe), ie.push({ event: ne, listeners: pe }));
        }
      }
      if ((n & 7) === 0) {
        e: {
          if (ne = e === "mouseover" || e === "pointerover", fe = e === "mouseout" || e === "pointerout", ne && o !== io && (he = o.relatedTarget || o.fromElement) && (Qn(he) || he[tn])) break e;
          if ((fe || ne) && (ne = oe.window === oe ? oe : (ne = oe.ownerDocument) ? ne.defaultView || ne.parentWindow : window, fe ? (he = o.relatedTarget || o.toElement, fe = Z, he = he ? Qn(he) : null, he !== null && (Be = Vt(he), he !== Be || he.tag !== 5 && he.tag !== 6) && (he = null)) : (fe = null, he = Z), fe !== he)) {
            if (pe = kc, se = "onMouseLeave", Y = "onMouseEnter", O = "mouse", (e === "pointerout" || e === "pointerover") && (pe = Cc, se = "onPointerLeave", Y = "onPointerEnter", O = "pointer"), Be = fe == null ? ne : Ir(fe), X = he == null ? ne : Ir(he), ne = new pe(se, O + "leave", fe, o, oe), ne.target = Be, ne.relatedTarget = X, se = null, Qn(oe) === Z && (pe = new pe(Y, O + "enter", he, o, oe), pe.target = X, pe.relatedTarget = Be, se = pe), Be = se, fe && he) t: {
              for (pe = fe, Y = he, O = 0, X = pe; X; X = Mr(X)) O++;
              for (X = 0, se = Y; se; se = Mr(se)) X++;
              for (; 0 < O - X; ) pe = Mr(pe), O--;
              for (; 0 < X - O; ) Y = Mr(Y), X--;
              for (; O--; ) {
                if (pe === Y || Y !== null && pe === Y.alternate) break t;
                pe = Mr(pe), Y = Mr(Y);
              }
              pe = null;
            }
            else pe = null;
            fe !== null && Jc(ie, ne, fe, pe, !1), he !== null && Be !== null && Jc(ie, Be, he, pe, !0);
          }
        }
        e: {
          if (ne = Z ? Ir(Z) : window, fe = ne.nodeName && ne.nodeName.toLowerCase(), fe === "select" || fe === "input" && ne.type === "file") var ge = Sm;
          else if (Lc(ne)) if (Rc) ge = Nm;
          else {
            ge = Em;
            var ve = _m;
          }
          else (fe = ne.nodeName) && fe.toLowerCase() === "input" && (ne.type === "checkbox" || ne.type === "radio") && (ge = km);
          if (ge && (ge = ge(e, Z))) {
            Ac(ie, ge, o, oe);
            break e;
          }
          ve && ve(e, ne, Z), e === "focusout" && (ve = ne._wrapperState) && ve.controlled && ne.type === "number" && dt(ne, "number", ne.value);
        }
        switch (ve = Z ? Ir(Z) : window, e) {
          case "focusin":
            (Lc(ve) || ve.contentEditable === "true") && (Nr = ve, eu = Z, Po = null);
            break;
          case "focusout":
            Po = eu = Nr = null;
            break;
          case "mousedown":
            tu = !0;
            break;
          case "contextmenu":
          case "mouseup":
          case "dragend":
            tu = !1, Bc(ie, o, oe);
            break;
          case "selectionchange":
            if (Pm) break;
          case "keydown":
          case "keyup":
            Bc(ie, o, oe);
        }
        var we;
        if (Gl) e: {
          switch (e) {
            case "compositionstart":
              var Ee = "onCompositionStart";
              break e;
            case "compositionend":
              Ee = "onCompositionEnd";
              break e;
            case "compositionupdate":
              Ee = "onCompositionUpdate";
              break e;
          }
          Ee = void 0;
        }
        else kr ? zc(e, o) && (Ee = "onCompositionEnd") : e === "keydown" && o.keyCode === 229 && (Ee = "onCompositionStart");
        Ee && (Mc && o.locale !== "ko" && (kr || Ee !== "onCompositionStart" ? Ee === "onCompositionEnd" && kr && (we = _c()) : (Sn = oe, Ul = "value" in Sn ? Sn.value : Sn.textContent, kr = !0)), ve = Ki(Z, Ee), 0 < ve.length && (Ee = new Nc(Ee, e, null, o, oe), ie.push({ event: Ee, listeners: ve }), we ? Ee.data = we : (we = Tc(o), we !== null && (Ee.data = we)))), (we = mm ? ym(e, o) : vm(e, o)) && (Z = Ki(Z, "onBeforeInput"), 0 < Z.length && (oe = new Nc("onBeforeInput", "beforeinput", null, o, oe), ie.push({ event: oe, listeners: Z }), oe.data = we));
      }
      Zc(ie, n);
    });
  }
  function To(e, n, o) {
    return { instance: e, listener: n, currentTarget: o };
  }
  function Ki(e, n) {
    for (var o = n + "Capture", s = []; e !== null; ) {
      var a = e, d = a.stateNode;
      a.tag === 5 && d !== null && (a = d, d = Wn(e, o), d != null && s.unshift(To(e, d, a)), d = Wn(e, n), d != null && s.push(To(e, d, a))), e = e.return;
    }
    return s;
  }
  function Mr(e) {
    if (e === null) return null;
    do
      e = e.return;
    while (e && e.tag !== 5);
    return e || null;
  }
  function Jc(e, n, o, s, a) {
    for (var d = n._reactName, w = []; o !== null && o !== s; ) {
      var P = o, R = P.alternate, Z = P.stateNode;
      if (R !== null && R === s) break;
      P.tag === 5 && Z !== null && (P = Z, a ? (R = Wn(o, d), R != null && w.unshift(To(o, R, P))) : a || (R = Wn(o, d), R != null && w.push(To(o, R, P)))), o = o.return;
    }
    w.length !== 0 && e.push({ event: n, listeners: w });
  }
  var Lm = /\r\n?/g, Am = /\u0000|\uFFFD/g;
  function ef(e) {
    return (typeof e == "string" ? e : "" + e).replace(Lm, `
`).replace(Am, "");
  }
  function Gi(e, n, o) {
    if (n = ef(n), ef(e) !== n && o) throw Error(i(425));
  }
  function Zi() {
  }
  var lu = null, uu = null;
  function au(e, n) {
    return e === "textarea" || e === "noscript" || typeof n.children == "string" || typeof n.children == "number" || typeof n.dangerouslySetInnerHTML == "object" && n.dangerouslySetInnerHTML !== null && n.dangerouslySetInnerHTML.__html != null;
  }
  var cu = typeof setTimeout == "function" ? setTimeout : void 0, Rm = typeof clearTimeout == "function" ? clearTimeout : void 0, tf = typeof Promise == "function" ? Promise : void 0, $m = typeof queueMicrotask == "function" ? queueMicrotask : typeof tf < "u" ? function(e) {
    return tf.resolve(null).then(e).catch(Dm);
  } : cu;
  function Dm(e) {
    setTimeout(function() {
      throw e;
    });
  }
  function fu(e, n) {
    var o = n, s = 0;
    do {
      var a = o.nextSibling;
      if (e.removeChild(o), a && a.nodeType === 8) if (o = a.data, o === "/$") {
        if (s === 0) {
          e.removeChild(a), So(n);
          return;
        }
        s--;
      } else o !== "$" && o !== "$?" && o !== "$!" || s++;
      o = a;
    } while (o);
    So(n);
  }
  function En(e) {
    for (; e != null; e = e.nextSibling) {
      var n = e.nodeType;
      if (n === 1 || n === 3) break;
      if (n === 8) {
        if (n = e.data, n === "$" || n === "$!" || n === "$?") break;
        if (n === "/$") return null;
      }
    }
    return e;
  }
  function nf(e) {
    e = e.previousSibling;
    for (var n = 0; e; ) {
      if (e.nodeType === 8) {
        var o = e.data;
        if (o === "$" || o === "$!" || o === "$?") {
          if (n === 0) return e;
          n--;
        } else o === "/$" && n++;
      }
      e = e.previousSibling;
    }
    return null;
  }
  var Pr = Math.random().toString(36).slice(2), Wt = "__reactFiber$" + Pr, Lo = "__reactProps$" + Pr, tn = "__reactContainer$" + Pr, du = "__reactEvents$" + Pr, jm = "__reactListeners$" + Pr, Om = "__reactHandles$" + Pr;
  function Qn(e) {
    var n = e[Wt];
    if (n) return n;
    for (var o = e.parentNode; o; ) {
      if (n = o[tn] || o[Wt]) {
        if (o = n.alternate, n.child !== null || o !== null && o.child !== null) for (e = nf(e); e !== null; ) {
          if (o = e[Wt]) return o;
          e = nf(e);
        }
        return n;
      }
      e = o, o = e.parentNode;
    }
    return null;
  }
  function Ao(e) {
    return e = e[Wt] || e[tn], !e || e.tag !== 5 && e.tag !== 6 && e.tag !== 13 && e.tag !== 3 ? null : e;
  }
  function Ir(e) {
    if (e.tag === 5 || e.tag === 6) return e.stateNode;
    throw Error(i(33));
  }
  function qi(e) {
    return e[Lo] || null;
  }
  var hu = [], zr = -1;
  function kn(e) {
    return { current: e };
  }
  function Re(e) {
    0 > zr || (e.current = hu[zr], hu[zr] = null, zr--);
  }
  function Le(e, n) {
    zr++, hu[zr] = e.current, e.current = n;
  }
  var Nn = {}, Je = kn(Nn), it = kn(!1), Kn = Nn;
  function Tr(e, n) {
    var o = e.type.contextTypes;
    if (!o) return Nn;
    var s = e.stateNode;
    if (s && s.__reactInternalMemoizedUnmaskedChildContext === n) return s.__reactInternalMemoizedMaskedChildContext;
    var a = {}, d;
    for (d in o) a[d] = n[d];
    return s && (e = e.stateNode, e.__reactInternalMemoizedUnmaskedChildContext = n, e.__reactInternalMemoizedMaskedChildContext = a), a;
  }
  function st(e) {
    return e = e.childContextTypes, e != null;
  }
  function Ji() {
    Re(it), Re(Je);
  }
  function rf(e, n, o) {
    if (Je.current !== Nn) throw Error(i(168));
    Le(Je, n), Le(it, o);
  }
  function of(e, n, o) {
    var s = e.stateNode;
    if (n = n.childContextTypes, typeof s.getChildContext != "function") return o;
    s = s.getChildContext();
    for (var a in s) if (!(a in n)) throw Error(i(108, ae(e) || "Unknown", a));
    return V({}, o, s);
  }
  function es(e) {
    return e = (e = e.stateNode) && e.__reactInternalMemoizedMergedChildContext || Nn, Kn = Je.current, Le(Je, e), Le(it, it.current), !0;
  }
  function sf(e, n, o) {
    var s = e.stateNode;
    if (!s) throw Error(i(169));
    o ? (e = of(e, n, Kn), s.__reactInternalMemoizedMergedChildContext = e, Re(it), Re(Je), Le(Je, e)) : Re(it), Le(it, o);
  }
  var nn = null, ts = !1, pu = !1;
  function lf(e) {
    nn === null ? nn = [e] : nn.push(e);
  }
  function Fm(e) {
    ts = !0, lf(e);
  }
  function Cn() {
    if (!pu && nn !== null) {
      pu = !0;
      var e = 0, n = ze;
      try {
        var o = nn;
        for (ze = 1; e < o.length; e++) {
          var s = o[e];
          do
            s = s(!0);
          while (s !== null);
        }
        nn = null, ts = !1;
      } catch (a) {
        throw nn !== null && (nn = nn.slice(e + 1)), zi(po, Cn), a;
      } finally {
        ze = n, pu = !1;
      }
    }
    return null;
  }
  var Lr = [], Ar = 0, ns = null, rs = 0, Et = [], kt = 0, Gn = null, rn = 1, on = "";
  function Zn(e, n) {
    Lr[Ar++] = rs, Lr[Ar++] = ns, ns = e, rs = n;
  }
  function uf(e, n, o) {
    Et[kt++] = rn, Et[kt++] = on, Et[kt++] = Gn, Gn = e;
    var s = rn;
    e = on;
    var a = 32 - pt(s) - 1;
    s &= ~(1 << a), o += 1;
    var d = 32 - pt(n) + a;
    if (30 < d) {
      var w = a - a % 5;
      d = (s & (1 << w) - 1).toString(32), s >>= w, a -= w, rn = 1 << 32 - pt(n) + a | o << a | s, on = d + e;
    } else rn = 1 << d | o << a | s, on = e;
  }
  function gu(e) {
    e.return !== null && (Zn(e, 1), uf(e, 1, 0));
  }
  function mu(e) {
    for (; e === ns; ) ns = Lr[--Ar], Lr[Ar] = null, rs = Lr[--Ar], Lr[Ar] = null;
    for (; e === Gn; ) Gn = Et[--kt], Et[kt] = null, on = Et[--kt], Et[kt] = null, rn = Et[--kt], Et[kt] = null;
  }
  var mt = null, yt = null, De = !1, Tt = null;
  function af(e, n) {
    var o = Pt(5, null, null, 0);
    o.elementType = "DELETED", o.stateNode = n, o.return = e, n = e.deletions, n === null ? (e.deletions = [o], e.flags |= 16) : n.push(o);
  }
  function cf(e, n) {
    switch (e.tag) {
      case 5:
        var o = e.type;
        return n = n.nodeType !== 1 || o.toLowerCase() !== n.nodeName.toLowerCase() ? null : n, n !== null ? (e.stateNode = n, mt = e, yt = En(n.firstChild), !0) : !1;
      case 6:
        return n = e.pendingProps === "" || n.nodeType !== 3 ? null : n, n !== null ? (e.stateNode = n, mt = e, yt = null, !0) : !1;
      case 13:
        return n = n.nodeType !== 8 ? null : n, n !== null ? (o = Gn !== null ? { id: rn, overflow: on } : null, e.memoizedState = { dehydrated: n, treeContext: o, retryLane: 1073741824 }, o = Pt(18, null, null, 0), o.stateNode = n, o.return = e, e.child = o, mt = e, yt = null, !0) : !1;
      default:
        return !1;
    }
  }
  function yu(e) {
    return (e.mode & 1) !== 0 && (e.flags & 128) === 0;
  }
  function vu(e) {
    if (De) {
      var n = yt;
      if (n) {
        var o = n;
        if (!cf(e, n)) {
          if (yu(e)) throw Error(i(418));
          n = En(o.nextSibling);
          var s = mt;
          n && cf(e, n) ? af(s, o) : (e.flags = e.flags & -4097 | 2, De = !1, mt = e);
        }
      } else {
        if (yu(e)) throw Error(i(418));
        e.flags = e.flags & -4097 | 2, De = !1, mt = e;
      }
    }
  }
  function ff(e) {
    for (e = e.return; e !== null && e.tag !== 5 && e.tag !== 3 && e.tag !== 13; ) e = e.return;
    mt = e;
  }
  function os(e) {
    if (e !== mt) return !1;
    if (!De) return ff(e), De = !0, !1;
    var n;
    if ((n = e.tag !== 3) && !(n = e.tag !== 5) && (n = e.type, n = n !== "head" && n !== "body" && !au(e.type, e.memoizedProps)), n && (n = yt)) {
      if (yu(e)) throw df(), Error(i(418));
      for (; n; ) af(e, n), n = En(n.nextSibling);
    }
    if (ff(e), e.tag === 13) {
      if (e = e.memoizedState, e = e !== null ? e.dehydrated : null, !e) throw Error(i(317));
      e: {
        for (e = e.nextSibling, n = 0; e; ) {
          if (e.nodeType === 8) {
            var o = e.data;
            if (o === "/$") {
              if (n === 0) {
                yt = En(e.nextSibling);
                break e;
              }
              n--;
            } else o !== "$" && o !== "$!" && o !== "$?" || n++;
          }
          e = e.nextSibling;
        }
        yt = null;
      }
    } else yt = mt ? En(e.stateNode.nextSibling) : null;
    return !0;
  }
  function df() {
    for (var e = yt; e; ) e = En(e.nextSibling);
  }
  function Rr() {
    yt = mt = null, De = !1;
  }
  function wu(e) {
    Tt === null ? Tt = [e] : Tt.push(e);
  }
  var Hm = E.ReactCurrentBatchConfig;
  function Ro(e, n, o) {
    if (e = o.ref, e !== null && typeof e != "function" && typeof e != "object") {
      if (o._owner) {
        if (o = o._owner, o) {
          if (o.tag !== 1) throw Error(i(309));
          var s = o.stateNode;
        }
        if (!s) throw Error(i(147, e));
        var a = s, d = "" + e;
        return n !== null && n.ref !== null && typeof n.ref == "function" && n.ref._stringRef === d ? n.ref : (n = function(w) {
          var P = a.refs;
          w === null ? delete P[d] : P[d] = w;
        }, n._stringRef = d, n);
      }
      if (typeof e != "string") throw Error(i(284));
      if (!o._owner) throw Error(i(290, e));
    }
    return e;
  }
  function is(e, n) {
    throw e = Object.prototype.toString.call(n), Error(i(31, e === "[object Object]" ? "object with keys {" + Object.keys(n).join(", ") + "}" : e));
  }
  function hf(e) {
    var n = e._init;
    return n(e._payload);
  }
  function pf(e) {
    function n(Y, O) {
      if (e) {
        var X = Y.deletions;
        X === null ? (Y.deletions = [O], Y.flags |= 16) : X.push(O);
      }
    }
    function o(Y, O) {
      if (!e) return null;
      for (; O !== null; ) n(Y, O), O = O.sibling;
      return null;
    }
    function s(Y, O) {
      for (Y = /* @__PURE__ */ new Map(); O !== null; ) O.key !== null ? Y.set(O.key, O) : Y.set(O.index, O), O = O.sibling;
      return Y;
    }
    function a(Y, O) {
      return Y = Rn(Y, O), Y.index = 0, Y.sibling = null, Y;
    }
    function d(Y, O, X) {
      return Y.index = X, e ? (X = Y.alternate, X !== null ? (X = X.index, X < O ? (Y.flags |= 2, O) : X) : (Y.flags |= 2, O)) : (Y.flags |= 1048576, O);
    }
    function w(Y) {
      return e && Y.alternate === null && (Y.flags |= 2), Y;
    }
    function P(Y, O, X, se) {
      return O === null || O.tag !== 6 ? (O = ca(X, Y.mode, se), O.return = Y, O) : (O = a(O, X), O.return = Y, O);
    }
    function R(Y, O, X, se) {
      var ge = X.type;
      return ge === H ? oe(Y, O, X.props.children, se, X.key) : O !== null && (O.elementType === ge || typeof ge == "object" && ge !== null && ge.$$typeof === F && hf(ge) === O.type) ? (se = a(O, X.props), se.ref = Ro(Y, O, X), se.return = Y, se) : (se = Is(X.type, X.key, X.props, null, Y.mode, se), se.ref = Ro(Y, O, X), se.return = Y, se);
    }
    function Z(Y, O, X, se) {
      return O === null || O.tag !== 4 || O.stateNode.containerInfo !== X.containerInfo || O.stateNode.implementation !== X.implementation ? (O = fa(X, Y.mode, se), O.return = Y, O) : (O = a(O, X.children || []), O.return = Y, O);
    }
    function oe(Y, O, X, se, ge) {
      return O === null || O.tag !== 7 ? (O = ir(X, Y.mode, se, ge), O.return = Y, O) : (O = a(O, X), O.return = Y, O);
    }
    function ie(Y, O, X) {
      if (typeof O == "string" && O !== "" || typeof O == "number") return O = ca("" + O, Y.mode, X), O.return = Y, O;
      if (typeof O == "object" && O !== null) {
        switch (O.$$typeof) {
          case D:
            return X = Is(O.type, O.key, O.props, null, Y.mode, X), X.ref = Ro(Y, null, O), X.return = Y, X;
          case j:
            return O = fa(O, Y.mode, X), O.return = Y, O;
          case F:
            var se = O._init;
            return ie(Y, se(O._payload), X);
        }
        if (ht(O) || L(O)) return O = ir(O, Y.mode, X, null), O.return = Y, O;
        is(Y, O);
      }
      return null;
    }
    function ne(Y, O, X, se) {
      var ge = O !== null ? O.key : null;
      if (typeof X == "string" && X !== "" || typeof X == "number") return ge !== null ? null : P(Y, O, "" + X, se);
      if (typeof X == "object" && X !== null) {
        switch (X.$$typeof) {
          case D:
            return X.key === ge ? R(Y, O, X, se) : null;
          case j:
            return X.key === ge ? Z(Y, O, X, se) : null;
          case F:
            return ge = X._init, ne(
              Y,
              O,
              ge(X._payload),
              se
            );
        }
        if (ht(X) || L(X)) return ge !== null ? null : oe(Y, O, X, se, null);
        is(Y, X);
      }
      return null;
    }
    function fe(Y, O, X, se, ge) {
      if (typeof se == "string" && se !== "" || typeof se == "number") return Y = Y.get(X) || null, P(O, Y, "" + se, ge);
      if (typeof se == "object" && se !== null) {
        switch (se.$$typeof) {
          case D:
            return Y = Y.get(se.key === null ? X : se.key) || null, R(O, Y, se, ge);
          case j:
            return Y = Y.get(se.key === null ? X : se.key) || null, Z(O, Y, se, ge);
          case F:
            var ve = se._init;
            return fe(Y, O, X, ve(se._payload), ge);
        }
        if (ht(se) || L(se)) return Y = Y.get(X) || null, oe(O, Y, se, ge, null);
        is(O, se);
      }
      return null;
    }
    function he(Y, O, X, se) {
      for (var ge = null, ve = null, we = O, Ee = O = 0, Ge = null; we !== null && Ee < X.length; Ee++) {
        we.index > Ee ? (Ge = we, we = null) : Ge = we.sibling;
        var Ie = ne(Y, we, X[Ee], se);
        if (Ie === null) {
          we === null && (we = Ge);
          break;
        }
        e && we && Ie.alternate === null && n(Y, we), O = d(Ie, O, Ee), ve === null ? ge = Ie : ve.sibling = Ie, ve = Ie, we = Ge;
      }
      if (Ee === X.length) return o(Y, we), De && Zn(Y, Ee), ge;
      if (we === null) {
        for (; Ee < X.length; Ee++) we = ie(Y, X[Ee], se), we !== null && (O = d(we, O, Ee), ve === null ? ge = we : ve.sibling = we, ve = we);
        return De && Zn(Y, Ee), ge;
      }
      for (we = s(Y, we); Ee < X.length; Ee++) Ge = fe(we, Y, Ee, X[Ee], se), Ge !== null && (e && Ge.alternate !== null && we.delete(Ge.key === null ? Ee : Ge.key), O = d(Ge, O, Ee), ve === null ? ge = Ge : ve.sibling = Ge, ve = Ge);
      return e && we.forEach(function($n) {
        return n(Y, $n);
      }), De && Zn(Y, Ee), ge;
    }
    function pe(Y, O, X, se) {
      var ge = L(X);
      if (typeof ge != "function") throw Error(i(150));
      if (X = ge.call(X), X == null) throw Error(i(151));
      for (var ve = ge = null, we = O, Ee = O = 0, Ge = null, Ie = X.next(); we !== null && !Ie.done; Ee++, Ie = X.next()) {
        we.index > Ee ? (Ge = we, we = null) : Ge = we.sibling;
        var $n = ne(Y, we, Ie.value, se);
        if ($n === null) {
          we === null && (we = Ge);
          break;
        }
        e && we && $n.alternate === null && n(Y, we), O = d($n, O, Ee), ve === null ? ge = $n : ve.sibling = $n, ve = $n, we = Ge;
      }
      if (Ie.done) return o(
        Y,
        we
      ), De && Zn(Y, Ee), ge;
      if (we === null) {
        for (; !Ie.done; Ee++, Ie = X.next()) Ie = ie(Y, Ie.value, se), Ie !== null && (O = d(Ie, O, Ee), ve === null ? ge = Ie : ve.sibling = Ie, ve = Ie);
        return De && Zn(Y, Ee), ge;
      }
      for (we = s(Y, we); !Ie.done; Ee++, Ie = X.next()) Ie = fe(we, Y, Ee, Ie.value, se), Ie !== null && (e && Ie.alternate !== null && we.delete(Ie.key === null ? Ee : Ie.key), O = d(Ie, O, Ee), ve === null ? ge = Ie : ve.sibling = Ie, ve = Ie);
      return e && we.forEach(function(w0) {
        return n(Y, w0);
      }), De && Zn(Y, Ee), ge;
    }
    function Be(Y, O, X, se) {
      if (typeof X == "object" && X !== null && X.type === H && X.key === null && (X = X.props.children), typeof X == "object" && X !== null) {
        switch (X.$$typeof) {
          case D:
            e: {
              for (var ge = X.key, ve = O; ve !== null; ) {
                if (ve.key === ge) {
                  if (ge = X.type, ge === H) {
                    if (ve.tag === 7) {
                      o(Y, ve.sibling), O = a(ve, X.props.children), O.return = Y, Y = O;
                      break e;
                    }
                  } else if (ve.elementType === ge || typeof ge == "object" && ge !== null && ge.$$typeof === F && hf(ge) === ve.type) {
                    o(Y, ve.sibling), O = a(ve, X.props), O.ref = Ro(Y, ve, X), O.return = Y, Y = O;
                    break e;
                  }
                  o(Y, ve);
                  break;
                } else n(Y, ve);
                ve = ve.sibling;
              }
              X.type === H ? (O = ir(X.props.children, Y.mode, se, X.key), O.return = Y, Y = O) : (se = Is(X.type, X.key, X.props, null, Y.mode, se), se.ref = Ro(Y, O, X), se.return = Y, Y = se);
            }
            return w(Y);
          case j:
            e: {
              for (ve = X.key; O !== null; ) {
                if (O.key === ve) if (O.tag === 4 && O.stateNode.containerInfo === X.containerInfo && O.stateNode.implementation === X.implementation) {
                  o(Y, O.sibling), O = a(O, X.children || []), O.return = Y, Y = O;
                  break e;
                } else {
                  o(Y, O);
                  break;
                }
                else n(Y, O);
                O = O.sibling;
              }
              O = fa(X, Y.mode, se), O.return = Y, Y = O;
            }
            return w(Y);
          case F:
            return ve = X._init, Be(Y, O, ve(X._payload), se);
        }
        if (ht(X)) return he(Y, O, X, se);
        if (L(X)) return pe(Y, O, X, se);
        is(Y, X);
      }
      return typeof X == "string" && X !== "" || typeof X == "number" ? (X = "" + X, O !== null && O.tag === 6 ? (o(Y, O.sibling), O = a(O, X), O.return = Y, Y = O) : (o(Y, O), O = ca(X, Y.mode, se), O.return = Y, Y = O), w(Y)) : o(Y, O);
    }
    return Be;
  }
  var $r = pf(!0), gf = pf(!1), ss = kn(null), ls = null, Dr = null, xu = null;
  function Su() {
    xu = Dr = ls = null;
  }
  function _u(e) {
    var n = ss.current;
    Re(ss), e._currentValue = n;
  }
  function Eu(e, n, o) {
    for (; e !== null; ) {
      var s = e.alternate;
      if ((e.childLanes & n) !== n ? (e.childLanes |= n, s !== null && (s.childLanes |= n)) : s !== null && (s.childLanes & n) !== n && (s.childLanes |= n), e === o) break;
      e = e.return;
    }
  }
  function jr(e, n) {
    ls = e, xu = Dr = null, e = e.dependencies, e !== null && e.firstContext !== null && ((e.lanes & n) !== 0 && (lt = !0), e.firstContext = null);
  }
  function Nt(e) {
    var n = e._currentValue;
    if (xu !== e) if (e = { context: e, memoizedValue: n, next: null }, Dr === null) {
      if (ls === null) throw Error(i(308));
      Dr = e, ls.dependencies = { lanes: 0, firstContext: e };
    } else Dr = Dr.next = e;
    return n;
  }
  var qn = null;
  function ku(e) {
    qn === null ? qn = [e] : qn.push(e);
  }
  function mf(e, n, o, s) {
    var a = n.interleaved;
    return a === null ? (o.next = o, ku(n)) : (o.next = a.next, a.next = o), n.interleaved = o, sn(e, s);
  }
  function sn(e, n) {
    e.lanes |= n;
    var o = e.alternate;
    for (o !== null && (o.lanes |= n), o = e, e = e.return; e !== null; ) e.childLanes |= n, o = e.alternate, o !== null && (o.childLanes |= n), o = e, e = e.return;
    return o.tag === 3 ? o.stateNode : null;
  }
  var Mn = !1;
  function Nu(e) {
    e.updateQueue = { baseState: e.memoizedState, firstBaseUpdate: null, lastBaseUpdate: null, shared: { pending: null, interleaved: null, lanes: 0 }, effects: null };
  }
  function yf(e, n) {
    e = e.updateQueue, n.updateQueue === e && (n.updateQueue = { baseState: e.baseState, firstBaseUpdate: e.firstBaseUpdate, lastBaseUpdate: e.lastBaseUpdate, shared: e.shared, effects: e.effects });
  }
  function ln(e, n) {
    return { eventTime: e, lane: n, tag: 0, payload: null, callback: null, next: null };
  }
  function Pn(e, n, o) {
    var s = e.updateQueue;
    if (s === null) return null;
    if (s = s.shared, (Pe & 2) !== 0) {
      var a = s.pending;
      return a === null ? n.next = n : (n.next = a.next, a.next = n), s.pending = n, sn(e, o);
    }
    return a = s.interleaved, a === null ? (n.next = n, ku(s)) : (n.next = a.next, a.next = n), s.interleaved = n, sn(e, o);
  }
  function us(e, n, o) {
    if (n = n.updateQueue, n !== null && (n = n.shared, (o & 4194240) !== 0)) {
      var s = n.lanes;
      s &= e.pendingLanes, o |= s, n.lanes = o, Fl(e, o);
    }
  }
  function vf(e, n) {
    var o = e.updateQueue, s = e.alternate;
    if (s !== null && (s = s.updateQueue, o === s)) {
      var a = null, d = null;
      if (o = o.firstBaseUpdate, o !== null) {
        do {
          var w = { eventTime: o.eventTime, lane: o.lane, tag: o.tag, payload: o.payload, callback: o.callback, next: null };
          d === null ? a = d = w : d = d.next = w, o = o.next;
        } while (o !== null);
        d === null ? a = d = n : d = d.next = n;
      } else a = d = n;
      o = { baseState: s.baseState, firstBaseUpdate: a, lastBaseUpdate: d, shared: s.shared, effects: s.effects }, e.updateQueue = o;
      return;
    }
    e = o.lastBaseUpdate, e === null ? o.firstBaseUpdate = n : e.next = n, o.lastBaseUpdate = n;
  }
  function as(e, n, o, s) {
    var a = e.updateQueue;
    Mn = !1;
    var d = a.firstBaseUpdate, w = a.lastBaseUpdate, P = a.shared.pending;
    if (P !== null) {
      a.shared.pending = null;
      var R = P, Z = R.next;
      R.next = null, w === null ? d = Z : w.next = Z, w = R;
      var oe = e.alternate;
      oe !== null && (oe = oe.updateQueue, P = oe.lastBaseUpdate, P !== w && (P === null ? oe.firstBaseUpdate = Z : P.next = Z, oe.lastBaseUpdate = R));
    }
    if (d !== null) {
      var ie = a.baseState;
      w = 0, oe = Z = R = null, P = d;
      do {
        var ne = P.lane, fe = P.eventTime;
        if ((s & ne) === ne) {
          oe !== null && (oe = oe.next = {
            eventTime: fe,
            lane: 0,
            tag: P.tag,
            payload: P.payload,
            callback: P.callback,
            next: null
          });
          e: {
            var he = e, pe = P;
            switch (ne = n, fe = o, pe.tag) {
              case 1:
                if (he = pe.payload, typeof he == "function") {
                  ie = he.call(fe, ie, ne);
                  break e;
                }
                ie = he;
                break e;
              case 3:
                he.flags = he.flags & -65537 | 128;
              case 0:
                if (he = pe.payload, ne = typeof he == "function" ? he.call(fe, ie, ne) : he, ne == null) break e;
                ie = V({}, ie, ne);
                break e;
              case 2:
                Mn = !0;
            }
          }
          P.callback !== null && P.lane !== 0 && (e.flags |= 64, ne = a.effects, ne === null ? a.effects = [P] : ne.push(P));
        } else fe = { eventTime: fe, lane: ne, tag: P.tag, payload: P.payload, callback: P.callback, next: null }, oe === null ? (Z = oe = fe, R = ie) : oe = oe.next = fe, w |= ne;
        if (P = P.next, P === null) {
          if (P = a.shared.pending, P === null) break;
          ne = P, P = ne.next, ne.next = null, a.lastBaseUpdate = ne, a.shared.pending = null;
        }
      } while (!0);
      if (oe === null && (R = ie), a.baseState = R, a.firstBaseUpdate = Z, a.lastBaseUpdate = oe, n = a.shared.interleaved, n !== null) {
        a = n;
        do
          w |= a.lane, a = a.next;
        while (a !== n);
      } else d === null && (a.shared.lanes = 0);
      tr |= w, e.lanes = w, e.memoizedState = ie;
    }
  }
  function wf(e, n, o) {
    if (e = n.effects, n.effects = null, e !== null) for (n = 0; n < e.length; n++) {
      var s = e[n], a = s.callback;
      if (a !== null) {
        if (s.callback = null, s = o, typeof a != "function") throw Error(i(191, a));
        a.call(s);
      }
    }
  }
  var $o = {}, Ut = kn($o), Do = kn($o), jo = kn($o);
  function Jn(e) {
    if (e === $o) throw Error(i(174));
    return e;
  }
  function Cu(e, n) {
    switch (Le(jo, n), Le(Do, e), Le(Ut, $o), e = n.nodeType, e) {
      case 9:
      case 11:
        n = (n = n.documentElement) ? n.namespaceURI : Jt(null, "");
        break;
      default:
        e = e === 8 ? n.parentNode : n, n = e.namespaceURI || null, e = e.tagName, n = Jt(n, e);
    }
    Re(Ut), Le(Ut, n);
  }
  function Or() {
    Re(Ut), Re(Do), Re(jo);
  }
  function xf(e) {
    Jn(jo.current);
    var n = Jn(Ut.current), o = Jt(n, e.type);
    n !== o && (Le(Do, e), Le(Ut, o));
  }
  function Mu(e) {
    Do.current === e && (Re(Ut), Re(Do));
  }
  var Fe = kn(0);
  function cs(e) {
    for (var n = e; n !== null; ) {
      if (n.tag === 13) {
        var o = n.memoizedState;
        if (o !== null && (o = o.dehydrated, o === null || o.data === "$?" || o.data === "$!")) return n;
      } else if (n.tag === 19 && n.memoizedProps.revealOrder !== void 0) {
        if ((n.flags & 128) !== 0) return n;
      } else if (n.child !== null) {
        n.child.return = n, n = n.child;
        continue;
      }
      if (n === e) break;
      for (; n.sibling === null; ) {
        if (n.return === null || n.return === e) return null;
        n = n.return;
      }
      n.sibling.return = n.return, n = n.sibling;
    }
    return null;
  }
  var Pu = [];
  function Iu() {
    for (var e = 0; e < Pu.length; e++) Pu[e]._workInProgressVersionPrimary = null;
    Pu.length = 0;
  }
  var fs = E.ReactCurrentDispatcher, zu = E.ReactCurrentBatchConfig, er = 0, He = null, be = null, Qe = null, ds = !1, Oo = !1, Fo = 0, Vm = 0;
  function et() {
    throw Error(i(321));
  }
  function Tu(e, n) {
    if (n === null) return !1;
    for (var o = 0; o < n.length && o < e.length; o++) if (!zt(e[o], n[o])) return !1;
    return !0;
  }
  function Lu(e, n, o, s, a, d) {
    if (er = d, He = n, n.memoizedState = null, n.updateQueue = null, n.lanes = 0, fs.current = e === null || e.memoizedState === null ? Ym : bm, e = o(s, a), Oo) {
      d = 0;
      do {
        if (Oo = !1, Fo = 0, 25 <= d) throw Error(i(301));
        d += 1, Qe = be = null, n.updateQueue = null, fs.current = Xm, e = o(s, a);
      } while (Oo);
    }
    if (fs.current = gs, n = be !== null && be.next !== null, er = 0, Qe = be = He = null, ds = !1, n) throw Error(i(300));
    return e;
  }
  function Au() {
    var e = Fo !== 0;
    return Fo = 0, e;
  }
  function Yt() {
    var e = { memoizedState: null, baseState: null, baseQueue: null, queue: null, next: null };
    return Qe === null ? He.memoizedState = Qe = e : Qe = Qe.next = e, Qe;
  }
  function Ct() {
    if (be === null) {
      var e = He.alternate;
      e = e !== null ? e.memoizedState : null;
    } else e = be.next;
    var n = Qe === null ? He.memoizedState : Qe.next;
    if (n !== null) Qe = n, be = e;
    else {
      if (e === null) throw Error(i(310));
      be = e, e = { memoizedState: be.memoizedState, baseState: be.baseState, baseQueue: be.baseQueue, queue: be.queue, next: null }, Qe === null ? He.memoizedState = Qe = e : Qe = Qe.next = e;
    }
    return Qe;
  }
  function Ho(e, n) {
    return typeof n == "function" ? n(e) : n;
  }
  function Ru(e) {
    var n = Ct(), o = n.queue;
    if (o === null) throw Error(i(311));
    o.lastRenderedReducer = e;
    var s = be, a = s.baseQueue, d = o.pending;
    if (d !== null) {
      if (a !== null) {
        var w = a.next;
        a.next = d.next, d.next = w;
      }
      s.baseQueue = a = d, o.pending = null;
    }
    if (a !== null) {
      d = a.next, s = s.baseState;
      var P = w = null, R = null, Z = d;
      do {
        var oe = Z.lane;
        if ((er & oe) === oe) R !== null && (R = R.next = { lane: 0, action: Z.action, hasEagerState: Z.hasEagerState, eagerState: Z.eagerState, next: null }), s = Z.hasEagerState ? Z.eagerState : e(s, Z.action);
        else {
          var ie = {
            lane: oe,
            action: Z.action,
            hasEagerState: Z.hasEagerState,
            eagerState: Z.eagerState,
            next: null
          };
          R === null ? (P = R = ie, w = s) : R = R.next = ie, He.lanes |= oe, tr |= oe;
        }
        Z = Z.next;
      } while (Z !== null && Z !== d);
      R === null ? w = s : R.next = P, zt(s, n.memoizedState) || (lt = !0), n.memoizedState = s, n.baseState = w, n.baseQueue = R, o.lastRenderedState = s;
    }
    if (e = o.interleaved, e !== null) {
      a = e;
      do
        d = a.lane, He.lanes |= d, tr |= d, a = a.next;
      while (a !== e);
    } else a === null && (o.lanes = 0);
    return [n.memoizedState, o.dispatch];
  }
  function $u(e) {
    var n = Ct(), o = n.queue;
    if (o === null) throw Error(i(311));
    o.lastRenderedReducer = e;
    var s = o.dispatch, a = o.pending, d = n.memoizedState;
    if (a !== null) {
      o.pending = null;
      var w = a = a.next;
      do
        d = e(d, w.action), w = w.next;
      while (w !== a);
      zt(d, n.memoizedState) || (lt = !0), n.memoizedState = d, n.baseQueue === null && (n.baseState = d), o.lastRenderedState = d;
    }
    return [d, s];
  }
  function Sf() {
  }
  function _f(e, n) {
    var o = He, s = Ct(), a = n(), d = !zt(s.memoizedState, a);
    if (d && (s.memoizedState = a, lt = !0), s = s.queue, Du(Nf.bind(null, o, s, e), [e]), s.getSnapshot !== n || d || Qe !== null && Qe.memoizedState.tag & 1) {
      if (o.flags |= 2048, Vo(9, kf.bind(null, o, s, a, n), void 0, null), Ke === null) throw Error(i(349));
      (er & 30) !== 0 || Ef(o, n, a);
    }
    return a;
  }
  function Ef(e, n, o) {
    e.flags |= 16384, e = { getSnapshot: n, value: o }, n = He.updateQueue, n === null ? (n = { lastEffect: null, stores: null }, He.updateQueue = n, n.stores = [e]) : (o = n.stores, o === null ? n.stores = [e] : o.push(e));
  }
  function kf(e, n, o, s) {
    n.value = o, n.getSnapshot = s, Cf(n) && Mf(e);
  }
  function Nf(e, n, o) {
    return o(function() {
      Cf(n) && Mf(e);
    });
  }
  function Cf(e) {
    var n = e.getSnapshot;
    e = e.value;
    try {
      var o = n();
      return !zt(e, o);
    } catch {
      return !0;
    }
  }
  function Mf(e) {
    var n = sn(e, 1);
    n !== null && $t(n, e, 1, -1);
  }
  function Pf(e) {
    var n = Yt();
    return typeof e == "function" && (e = e()), n.memoizedState = n.baseState = e, e = { pending: null, interleaved: null, lanes: 0, dispatch: null, lastRenderedReducer: Ho, lastRenderedState: e }, n.queue = e, e = e.dispatch = Um.bind(null, He, e), [n.memoizedState, e];
  }
  function Vo(e, n, o, s) {
    return e = { tag: e, create: n, destroy: o, deps: s, next: null }, n = He.updateQueue, n === null ? (n = { lastEffect: null, stores: null }, He.updateQueue = n, n.lastEffect = e.next = e) : (o = n.lastEffect, o === null ? n.lastEffect = e.next = e : (s = o.next, o.next = e, e.next = s, n.lastEffect = e)), e;
  }
  function If() {
    return Ct().memoizedState;
  }
  function hs(e, n, o, s) {
    var a = Yt();
    He.flags |= e, a.memoizedState = Vo(1 | n, o, void 0, s === void 0 ? null : s);
  }
  function ps(e, n, o, s) {
    var a = Ct();
    s = s === void 0 ? null : s;
    var d = void 0;
    if (be !== null) {
      var w = be.memoizedState;
      if (d = w.destroy, s !== null && Tu(s, w.deps)) {
        a.memoizedState = Vo(n, o, d, s);
        return;
      }
    }
    He.flags |= e, a.memoizedState = Vo(1 | n, o, d, s);
  }
  function zf(e, n) {
    return hs(8390656, 8, e, n);
  }
  function Du(e, n) {
    return ps(2048, 8, e, n);
  }
  function Tf(e, n) {
    return ps(4, 2, e, n);
  }
  function Lf(e, n) {
    return ps(4, 4, e, n);
  }
  function Af(e, n) {
    if (typeof n == "function") return e = e(), n(e), function() {
      n(null);
    };
    if (n != null) return e = e(), n.current = e, function() {
      n.current = null;
    };
  }
  function Rf(e, n, o) {
    return o = o != null ? o.concat([e]) : null, ps(4, 4, Af.bind(null, n, e), o);
  }
  function ju() {
  }
  function $f(e, n) {
    var o = Ct();
    n = n === void 0 ? null : n;
    var s = o.memoizedState;
    return s !== null && n !== null && Tu(n, s[1]) ? s[0] : (o.memoizedState = [e, n], e);
  }
  function Df(e, n) {
    var o = Ct();
    n = n === void 0 ? null : n;
    var s = o.memoizedState;
    return s !== null && n !== null && Tu(n, s[1]) ? s[0] : (e = e(), o.memoizedState = [e, n], e);
  }
  function jf(e, n, o) {
    return (er & 21) === 0 ? (e.baseState && (e.baseState = !1, lt = !0), e.memoizedState = o) : (zt(o, n) || (o = Sr(), He.lanes |= o, tr |= o, e.baseState = !0), n);
  }
  function Bm(e, n) {
    var o = ze;
    ze = o !== 0 && 4 > o ? o : 4, e(!0);
    var s = zu.transition;
    zu.transition = {};
    try {
      e(!1), n();
    } finally {
      ze = o, zu.transition = s;
    }
  }
  function Of() {
    return Ct().memoizedState;
  }
  function Wm(e, n, o) {
    var s = Ln(e);
    if (o = { lane: s, action: o, hasEagerState: !1, eagerState: null, next: null }, Ff(e)) Hf(n, o);
    else if (o = mf(e, n, o, s), o !== null) {
      var a = ot();
      $t(o, e, s, a), Vf(o, n, s);
    }
  }
  function Um(e, n, o) {
    var s = Ln(e), a = { lane: s, action: o, hasEagerState: !1, eagerState: null, next: null };
    if (Ff(e)) Hf(n, a);
    else {
      var d = e.alternate;
      if (e.lanes === 0 && (d === null || d.lanes === 0) && (d = n.lastRenderedReducer, d !== null)) try {
        var w = n.lastRenderedState, P = d(w, o);
        if (a.hasEagerState = !0, a.eagerState = P, zt(P, w)) {
          var R = n.interleaved;
          R === null ? (a.next = a, ku(n)) : (a.next = R.next, R.next = a), n.interleaved = a;
          return;
        }
      } catch {
      } finally {
      }
      o = mf(e, n, a, s), o !== null && (a = ot(), $t(o, e, s, a), Vf(o, n, s));
    }
  }
  function Ff(e) {
    var n = e.alternate;
    return e === He || n !== null && n === He;
  }
  function Hf(e, n) {
    Oo = ds = !0;
    var o = e.pending;
    o === null ? n.next = n : (n.next = o.next, o.next = n), e.pending = n;
  }
  function Vf(e, n, o) {
    if ((o & 4194240) !== 0) {
      var s = n.lanes;
      s &= e.pendingLanes, o |= s, n.lanes = o, Fl(e, o);
    }
  }
  var gs = { readContext: Nt, useCallback: et, useContext: et, useEffect: et, useImperativeHandle: et, useInsertionEffect: et, useLayoutEffect: et, useMemo: et, useReducer: et, useRef: et, useState: et, useDebugValue: et, useDeferredValue: et, useTransition: et, useMutableSource: et, useSyncExternalStore: et, useId: et, unstable_isNewReconciler: !1 }, Ym = { readContext: Nt, useCallback: function(e, n) {
    return Yt().memoizedState = [e, n === void 0 ? null : n], e;
  }, useContext: Nt, useEffect: zf, useImperativeHandle: function(e, n, o) {
    return o = o != null ? o.concat([e]) : null, hs(
      4194308,
      4,
      Af.bind(null, n, e),
      o
    );
  }, useLayoutEffect: function(e, n) {
    return hs(4194308, 4, e, n);
  }, useInsertionEffect: function(e, n) {
    return hs(4, 2, e, n);
  }, useMemo: function(e, n) {
    var o = Yt();
    return n = n === void 0 ? null : n, e = e(), o.memoizedState = [e, n], e;
  }, useReducer: function(e, n, o) {
    var s = Yt();
    return n = o !== void 0 ? o(n) : n, s.memoizedState = s.baseState = n, e = { pending: null, interleaved: null, lanes: 0, dispatch: null, lastRenderedReducer: e, lastRenderedState: n }, s.queue = e, e = e.dispatch = Wm.bind(null, He, e), [s.memoizedState, e];
  }, useRef: function(e) {
    var n = Yt();
    return e = { current: e }, n.memoizedState = e;
  }, useState: Pf, useDebugValue: ju, useDeferredValue: function(e) {
    return Yt().memoizedState = e;
  }, useTransition: function() {
    var e = Pf(!1), n = e[0];
    return e = Bm.bind(null, e[1]), Yt().memoizedState = e, [n, e];
  }, useMutableSource: function() {
  }, useSyncExternalStore: function(e, n, o) {
    var s = He, a = Yt();
    if (De) {
      if (o === void 0) throw Error(i(407));
      o = o();
    } else {
      if (o = n(), Ke === null) throw Error(i(349));
      (er & 30) !== 0 || Ef(s, n, o);
    }
    a.memoizedState = o;
    var d = { value: o, getSnapshot: n };
    return a.queue = d, zf(Nf.bind(
      null,
      s,
      d,
      e
    ), [e]), s.flags |= 2048, Vo(9, kf.bind(null, s, d, o, n), void 0, null), o;
  }, useId: function() {
    var e = Yt(), n = Ke.identifierPrefix;
    if (De) {
      var o = on, s = rn;
      o = (s & ~(1 << 32 - pt(s) - 1)).toString(32) + o, n = ":" + n + "R" + o, o = Fo++, 0 < o && (n += "H" + o.toString(32)), n += ":";
    } else o = Vm++, n = ":" + n + "r" + o.toString(32) + ":";
    return e.memoizedState = n;
  }, unstable_isNewReconciler: !1 }, bm = {
    readContext: Nt,
    useCallback: $f,
    useContext: Nt,
    useEffect: Du,
    useImperativeHandle: Rf,
    useInsertionEffect: Tf,
    useLayoutEffect: Lf,
    useMemo: Df,
    useReducer: Ru,
    useRef: If,
    useState: function() {
      return Ru(Ho);
    },
    useDebugValue: ju,
    useDeferredValue: function(e) {
      var n = Ct();
      return jf(n, be.memoizedState, e);
    },
    useTransition: function() {
      var e = Ru(Ho)[0], n = Ct().memoizedState;
      return [e, n];
    },
    useMutableSource: Sf,
    useSyncExternalStore: _f,
    useId: Of,
    unstable_isNewReconciler: !1
  }, Xm = { readContext: Nt, useCallback: $f, useContext: Nt, useEffect: Du, useImperativeHandle: Rf, useInsertionEffect: Tf, useLayoutEffect: Lf, useMemo: Df, useReducer: $u, useRef: If, useState: function() {
    return $u(Ho);
  }, useDebugValue: ju, useDeferredValue: function(e) {
    var n = Ct();
    return be === null ? n.memoizedState = e : jf(n, be.memoizedState, e);
  }, useTransition: function() {
    var e = $u(Ho)[0], n = Ct().memoizedState;
    return [e, n];
  }, useMutableSource: Sf, useSyncExternalStore: _f, useId: Of, unstable_isNewReconciler: !1 };
  function Lt(e, n) {
    if (e && e.defaultProps) {
      n = V({}, n), e = e.defaultProps;
      for (var o in e) n[o] === void 0 && (n[o] = e[o]);
      return n;
    }
    return n;
  }
  function Ou(e, n, o, s) {
    n = e.memoizedState, o = o(s, n), o = o == null ? n : V({}, n, o), e.memoizedState = o, e.lanes === 0 && (e.updateQueue.baseState = o);
  }
  var ms = { isMounted: function(e) {
    return (e = e._reactInternals) ? Vt(e) === e : !1;
  }, enqueueSetState: function(e, n, o) {
    e = e._reactInternals;
    var s = ot(), a = Ln(e), d = ln(s, a);
    d.payload = n, o != null && (d.callback = o), n = Pn(e, d, a), n !== null && ($t(n, e, a, s), us(n, e, a));
  }, enqueueReplaceState: function(e, n, o) {
    e = e._reactInternals;
    var s = ot(), a = Ln(e), d = ln(s, a);
    d.tag = 1, d.payload = n, o != null && (d.callback = o), n = Pn(e, d, a), n !== null && ($t(n, e, a, s), us(n, e, a));
  }, enqueueForceUpdate: function(e, n) {
    e = e._reactInternals;
    var o = ot(), s = Ln(e), a = ln(o, s);
    a.tag = 2, n != null && (a.callback = n), n = Pn(e, a, s), n !== null && ($t(n, e, s, o), us(n, e, s));
  } };
  function Bf(e, n, o, s, a, d, w) {
    return e = e.stateNode, typeof e.shouldComponentUpdate == "function" ? e.shouldComponentUpdate(s, d, w) : n.prototype && n.prototype.isPureReactComponent ? !Mo(o, s) || !Mo(a, d) : !0;
  }
  function Wf(e, n, o) {
    var s = !1, a = Nn, d = n.contextType;
    return typeof d == "object" && d !== null ? d = Nt(d) : (a = st(n) ? Kn : Je.current, s = n.contextTypes, d = (s = s != null) ? Tr(e, a) : Nn), n = new n(o, d), e.memoizedState = n.state !== null && n.state !== void 0 ? n.state : null, n.updater = ms, e.stateNode = n, n._reactInternals = e, s && (e = e.stateNode, e.__reactInternalMemoizedUnmaskedChildContext = a, e.__reactInternalMemoizedMaskedChildContext = d), n;
  }
  function Uf(e, n, o, s) {
    e = n.state, typeof n.componentWillReceiveProps == "function" && n.componentWillReceiveProps(o, s), typeof n.UNSAFE_componentWillReceiveProps == "function" && n.UNSAFE_componentWillReceiveProps(o, s), n.state !== e && ms.enqueueReplaceState(n, n.state, null);
  }
  function Fu(e, n, o, s) {
    var a = e.stateNode;
    a.props = o, a.state = e.memoizedState, a.refs = {}, Nu(e);
    var d = n.contextType;
    typeof d == "object" && d !== null ? a.context = Nt(d) : (d = st(n) ? Kn : Je.current, a.context = Tr(e, d)), a.state = e.memoizedState, d = n.getDerivedStateFromProps, typeof d == "function" && (Ou(e, n, d, o), a.state = e.memoizedState), typeof n.getDerivedStateFromProps == "function" || typeof a.getSnapshotBeforeUpdate == "function" || typeof a.UNSAFE_componentWillMount != "function" && typeof a.componentWillMount != "function" || (n = a.state, typeof a.componentWillMount == "function" && a.componentWillMount(), typeof a.UNSAFE_componentWillMount == "function" && a.UNSAFE_componentWillMount(), n !== a.state && ms.enqueueReplaceState(a, a.state, null), as(e, o, a, s), a.state = e.memoizedState), typeof a.componentDidMount == "function" && (e.flags |= 4194308);
  }
  function Fr(e, n) {
    try {
      var o = "", s = n;
      do
        o += le(s), s = s.return;
      while (s);
      var a = o;
    } catch (d) {
      a = `
Error generating stack: ` + d.message + `
` + d.stack;
    }
    return { value: e, source: n, stack: a, digest: null };
  }
  function Hu(e, n, o) {
    return { value: e, source: null, stack: o ?? null, digest: n ?? null };
  }
  function Vu(e, n) {
    try {
      console.error(n.value);
    } catch (o) {
      setTimeout(function() {
        throw o;
      });
    }
  }
  var Qm = typeof WeakMap == "function" ? WeakMap : Map;
  function Yf(e, n, o) {
    o = ln(-1, o), o.tag = 3, o.payload = { element: null };
    var s = n.value;
    return o.callback = function() {
      Es || (Es = !0, na = s), Vu(e, n);
    }, o;
  }
  function bf(e, n, o) {
    o = ln(-1, o), o.tag = 3;
    var s = e.type.getDerivedStateFromError;
    if (typeof s == "function") {
      var a = n.value;
      o.payload = function() {
        return s(a);
      }, o.callback = function() {
        Vu(e, n);
      };
    }
    var d = e.stateNode;
    return d !== null && typeof d.componentDidCatch == "function" && (o.callback = function() {
      Vu(e, n), typeof s != "function" && (zn === null ? zn = /* @__PURE__ */ new Set([this]) : zn.add(this));
      var w = n.stack;
      this.componentDidCatch(n.value, { componentStack: w !== null ? w : "" });
    }), o;
  }
  function Xf(e, n, o) {
    var s = e.pingCache;
    if (s === null) {
      s = e.pingCache = new Qm();
      var a = /* @__PURE__ */ new Set();
      s.set(n, a);
    } else a = s.get(n), a === void 0 && (a = /* @__PURE__ */ new Set(), s.set(n, a));
    a.has(o) || (a.add(o), e = u0.bind(null, e, n, o), n.then(e, e));
  }
  function Qf(e) {
    do {
      var n;
      if ((n = e.tag === 13) && (n = e.memoizedState, n = n !== null ? n.dehydrated !== null : !0), n) return e;
      e = e.return;
    } while (e !== null);
    return null;
  }
  function Kf(e, n, o, s, a) {
    return (e.mode & 1) === 0 ? (e === n ? e.flags |= 65536 : (e.flags |= 128, o.flags |= 131072, o.flags &= -52805, o.tag === 1 && (o.alternate === null ? o.tag = 17 : (n = ln(-1, 1), n.tag = 2, Pn(o, n, 1))), o.lanes |= 1), e) : (e.flags |= 65536, e.lanes = a, e);
  }
  var Km = E.ReactCurrentOwner, lt = !1;
  function rt(e, n, o, s) {
    n.child = e === null ? gf(n, null, o, s) : $r(n, e.child, o, s);
  }
  function Gf(e, n, o, s, a) {
    o = o.render;
    var d = n.ref;
    return jr(n, a), s = Lu(e, n, o, s, d, a), o = Au(), e !== null && !lt ? (n.updateQueue = e.updateQueue, n.flags &= -2053, e.lanes &= ~a, un(e, n, a)) : (De && o && gu(n), n.flags |= 1, rt(e, n, s, a), n.child);
  }
  function Zf(e, n, o, s, a) {
    if (e === null) {
      var d = o.type;
      return typeof d == "function" && !aa(d) && d.defaultProps === void 0 && o.compare === null && o.defaultProps === void 0 ? (n.tag = 15, n.type = d, qf(e, n, d, s, a)) : (e = Is(o.type, null, s, n, n.mode, a), e.ref = n.ref, e.return = n, n.child = e);
    }
    if (d = e.child, (e.lanes & a) === 0) {
      var w = d.memoizedProps;
      if (o = o.compare, o = o !== null ? o : Mo, o(w, s) && e.ref === n.ref) return un(e, n, a);
    }
    return n.flags |= 1, e = Rn(d, s), e.ref = n.ref, e.return = n, n.child = e;
  }
  function qf(e, n, o, s, a) {
    if (e !== null) {
      var d = e.memoizedProps;
      if (Mo(d, s) && e.ref === n.ref) if (lt = !1, n.pendingProps = s = d, (e.lanes & a) !== 0) (e.flags & 131072) !== 0 && (lt = !0);
      else return n.lanes = e.lanes, un(e, n, a);
    }
    return Bu(e, n, o, s, a);
  }
  function Jf(e, n, o) {
    var s = n.pendingProps, a = s.children, d = e !== null ? e.memoizedState : null;
    if (s.mode === "hidden") if ((n.mode & 1) === 0) n.memoizedState = { baseLanes: 0, cachePool: null, transitions: null }, Le(Vr, vt), vt |= o;
    else {
      if ((o & 1073741824) === 0) return e = d !== null ? d.baseLanes | o : o, n.lanes = n.childLanes = 1073741824, n.memoizedState = { baseLanes: e, cachePool: null, transitions: null }, n.updateQueue = null, Le(Vr, vt), vt |= e, null;
      n.memoizedState = { baseLanes: 0, cachePool: null, transitions: null }, s = d !== null ? d.baseLanes : o, Le(Vr, vt), vt |= s;
    }
    else d !== null ? (s = d.baseLanes | o, n.memoizedState = null) : s = o, Le(Vr, vt), vt |= s;
    return rt(e, n, a, o), n.child;
  }
  function ed(e, n) {
    var o = n.ref;
    (e === null && o !== null || e !== null && e.ref !== o) && (n.flags |= 512, n.flags |= 2097152);
  }
  function Bu(e, n, o, s, a) {
    var d = st(o) ? Kn : Je.current;
    return d = Tr(n, d), jr(n, a), o = Lu(e, n, o, s, d, a), s = Au(), e !== null && !lt ? (n.updateQueue = e.updateQueue, n.flags &= -2053, e.lanes &= ~a, un(e, n, a)) : (De && s && gu(n), n.flags |= 1, rt(e, n, o, a), n.child);
  }
  function td(e, n, o, s, a) {
    if (st(o)) {
      var d = !0;
      es(n);
    } else d = !1;
    if (jr(n, a), n.stateNode === null) vs(e, n), Wf(n, o, s), Fu(n, o, s, a), s = !0;
    else if (e === null) {
      var w = n.stateNode, P = n.memoizedProps;
      w.props = P;
      var R = w.context, Z = o.contextType;
      typeof Z == "object" && Z !== null ? Z = Nt(Z) : (Z = st(o) ? Kn : Je.current, Z = Tr(n, Z));
      var oe = o.getDerivedStateFromProps, ie = typeof oe == "function" || typeof w.getSnapshotBeforeUpdate == "function";
      ie || typeof w.UNSAFE_componentWillReceiveProps != "function" && typeof w.componentWillReceiveProps != "function" || (P !== s || R !== Z) && Uf(n, w, s, Z), Mn = !1;
      var ne = n.memoizedState;
      w.state = ne, as(n, s, w, a), R = n.memoizedState, P !== s || ne !== R || it.current || Mn ? (typeof oe == "function" && (Ou(n, o, oe, s), R = n.memoizedState), (P = Mn || Bf(n, o, P, s, ne, R, Z)) ? (ie || typeof w.UNSAFE_componentWillMount != "function" && typeof w.componentWillMount != "function" || (typeof w.componentWillMount == "function" && w.componentWillMount(), typeof w.UNSAFE_componentWillMount == "function" && w.UNSAFE_componentWillMount()), typeof w.componentDidMount == "function" && (n.flags |= 4194308)) : (typeof w.componentDidMount == "function" && (n.flags |= 4194308), n.memoizedProps = s, n.memoizedState = R), w.props = s, w.state = R, w.context = Z, s = P) : (typeof w.componentDidMount == "function" && (n.flags |= 4194308), s = !1);
    } else {
      w = n.stateNode, yf(e, n), P = n.memoizedProps, Z = n.type === n.elementType ? P : Lt(n.type, P), w.props = Z, ie = n.pendingProps, ne = w.context, R = o.contextType, typeof R == "object" && R !== null ? R = Nt(R) : (R = st(o) ? Kn : Je.current, R = Tr(n, R));
      var fe = o.getDerivedStateFromProps;
      (oe = typeof fe == "function" || typeof w.getSnapshotBeforeUpdate == "function") || typeof w.UNSAFE_componentWillReceiveProps != "function" && typeof w.componentWillReceiveProps != "function" || (P !== ie || ne !== R) && Uf(n, w, s, R), Mn = !1, ne = n.memoizedState, w.state = ne, as(n, s, w, a);
      var he = n.memoizedState;
      P !== ie || ne !== he || it.current || Mn ? (typeof fe == "function" && (Ou(n, o, fe, s), he = n.memoizedState), (Z = Mn || Bf(n, o, Z, s, ne, he, R) || !1) ? (oe || typeof w.UNSAFE_componentWillUpdate != "function" && typeof w.componentWillUpdate != "function" || (typeof w.componentWillUpdate == "function" && w.componentWillUpdate(s, he, R), typeof w.UNSAFE_componentWillUpdate == "function" && w.UNSAFE_componentWillUpdate(s, he, R)), typeof w.componentDidUpdate == "function" && (n.flags |= 4), typeof w.getSnapshotBeforeUpdate == "function" && (n.flags |= 1024)) : (typeof w.componentDidUpdate != "function" || P === e.memoizedProps && ne === e.memoizedState || (n.flags |= 4), typeof w.getSnapshotBeforeUpdate != "function" || P === e.memoizedProps && ne === e.memoizedState || (n.flags |= 1024), n.memoizedProps = s, n.memoizedState = he), w.props = s, w.state = he, w.context = R, s = Z) : (typeof w.componentDidUpdate != "function" || P === e.memoizedProps && ne === e.memoizedState || (n.flags |= 4), typeof w.getSnapshotBeforeUpdate != "function" || P === e.memoizedProps && ne === e.memoizedState || (n.flags |= 1024), s = !1);
    }
    return Wu(e, n, o, s, d, a);
  }
  function Wu(e, n, o, s, a, d) {
    ed(e, n);
    var w = (n.flags & 128) !== 0;
    if (!s && !w) return a && sf(n, o, !1), un(e, n, d);
    s = n.stateNode, Km.current = n;
    var P = w && typeof o.getDerivedStateFromError != "function" ? null : s.render();
    return n.flags |= 1, e !== null && w ? (n.child = $r(n, e.child, null, d), n.child = $r(n, null, P, d)) : rt(e, n, P, d), n.memoizedState = s.state, a && sf(n, o, !0), n.child;
  }
  function nd(e) {
    var n = e.stateNode;
    n.pendingContext ? rf(e, n.pendingContext, n.pendingContext !== n.context) : n.context && rf(e, n.context, !1), Cu(e, n.containerInfo);
  }
  function rd(e, n, o, s, a) {
    return Rr(), wu(a), n.flags |= 256, rt(e, n, o, s), n.child;
  }
  var Uu = { dehydrated: null, treeContext: null, retryLane: 0 };
  function Yu(e) {
    return { baseLanes: e, cachePool: null, transitions: null };
  }
  function od(e, n, o) {
    var s = n.pendingProps, a = Fe.current, d = !1, w = (n.flags & 128) !== 0, P;
    if ((P = w) || (P = e !== null && e.memoizedState === null ? !1 : (a & 2) !== 0), P ? (d = !0, n.flags &= -129) : (e === null || e.memoizedState !== null) && (a |= 1), Le(Fe, a & 1), e === null)
      return vu(n), e = n.memoizedState, e !== null && (e = e.dehydrated, e !== null) ? ((n.mode & 1) === 0 ? n.lanes = 1 : e.data === "$!" ? n.lanes = 8 : n.lanes = 1073741824, null) : (w = s.children, e = s.fallback, d ? (s = n.mode, d = n.child, w = { mode: "hidden", children: w }, (s & 1) === 0 && d !== null ? (d.childLanes = 0, d.pendingProps = w) : d = zs(w, s, 0, null), e = ir(e, s, o, null), d.return = n, e.return = n, d.sibling = e, n.child = d, n.child.memoizedState = Yu(o), n.memoizedState = Uu, e) : bu(n, w));
    if (a = e.memoizedState, a !== null && (P = a.dehydrated, P !== null)) return Gm(e, n, w, s, P, a, o);
    if (d) {
      d = s.fallback, w = n.mode, a = e.child, P = a.sibling;
      var R = { mode: "hidden", children: s.children };
      return (w & 1) === 0 && n.child !== a ? (s = n.child, s.childLanes = 0, s.pendingProps = R, n.deletions = null) : (s = Rn(a, R), s.subtreeFlags = a.subtreeFlags & 14680064), P !== null ? d = Rn(P, d) : (d = ir(d, w, o, null), d.flags |= 2), d.return = n, s.return = n, s.sibling = d, n.child = s, s = d, d = n.child, w = e.child.memoizedState, w = w === null ? Yu(o) : { baseLanes: w.baseLanes | o, cachePool: null, transitions: w.transitions }, d.memoizedState = w, d.childLanes = e.childLanes & ~o, n.memoizedState = Uu, s;
    }
    return d = e.child, e = d.sibling, s = Rn(d, { mode: "visible", children: s.children }), (n.mode & 1) === 0 && (s.lanes = o), s.return = n, s.sibling = null, e !== null && (o = n.deletions, o === null ? (n.deletions = [e], n.flags |= 16) : o.push(e)), n.child = s, n.memoizedState = null, s;
  }
  function bu(e, n) {
    return n = zs({ mode: "visible", children: n }, e.mode, 0, null), n.return = e, e.child = n;
  }
  function ys(e, n, o, s) {
    return s !== null && wu(s), $r(n, e.child, null, o), e = bu(n, n.pendingProps.children), e.flags |= 2, n.memoizedState = null, e;
  }
  function Gm(e, n, o, s, a, d, w) {
    if (o)
      return n.flags & 256 ? (n.flags &= -257, s = Hu(Error(i(422))), ys(e, n, w, s)) : n.memoizedState !== null ? (n.child = e.child, n.flags |= 128, null) : (d = s.fallback, a = n.mode, s = zs({ mode: "visible", children: s.children }, a, 0, null), d = ir(d, a, w, null), d.flags |= 2, s.return = n, d.return = n, s.sibling = d, n.child = s, (n.mode & 1) !== 0 && $r(n, e.child, null, w), n.child.memoizedState = Yu(w), n.memoizedState = Uu, d);
    if ((n.mode & 1) === 0) return ys(e, n, w, null);
    if (a.data === "$!") {
      if (s = a.nextSibling && a.nextSibling.dataset, s) var P = s.dgst;
      return s = P, d = Error(i(419)), s = Hu(d, s, void 0), ys(e, n, w, s);
    }
    if (P = (w & e.childLanes) !== 0, lt || P) {
      if (s = Ke, s !== null) {
        switch (w & -w) {
          case 4:
            a = 2;
            break;
          case 16:
            a = 8;
            break;
          case 64:
          case 128:
          case 256:
          case 512:
          case 1024:
          case 2048:
          case 4096:
          case 8192:
          case 16384:
          case 32768:
          case 65536:
          case 131072:
          case 262144:
          case 524288:
          case 1048576:
          case 2097152:
          case 4194304:
          case 8388608:
          case 16777216:
          case 33554432:
          case 67108864:
            a = 32;
            break;
          case 536870912:
            a = 268435456;
            break;
          default:
            a = 0;
        }
        a = (a & (s.suspendedLanes | w)) !== 0 ? 0 : a, a !== 0 && a !== d.retryLane && (d.retryLane = a, sn(e, a), $t(s, e, a, -1));
      }
      return ua(), s = Hu(Error(i(421))), ys(e, n, w, s);
    }
    return a.data === "$?" ? (n.flags |= 128, n.child = e.child, n = a0.bind(null, e), a._reactRetry = n, null) : (e = d.treeContext, yt = En(a.nextSibling), mt = n, De = !0, Tt = null, e !== null && (Et[kt++] = rn, Et[kt++] = on, Et[kt++] = Gn, rn = e.id, on = e.overflow, Gn = n), n = bu(n, s.children), n.flags |= 4096, n);
  }
  function id(e, n, o) {
    e.lanes |= n;
    var s = e.alternate;
    s !== null && (s.lanes |= n), Eu(e.return, n, o);
  }
  function Xu(e, n, o, s, a) {
    var d = e.memoizedState;
    d === null ? e.memoizedState = { isBackwards: n, rendering: null, renderingStartTime: 0, last: s, tail: o, tailMode: a } : (d.isBackwards = n, d.rendering = null, d.renderingStartTime = 0, d.last = s, d.tail = o, d.tailMode = a);
  }
  function sd(e, n, o) {
    var s = n.pendingProps, a = s.revealOrder, d = s.tail;
    if (rt(e, n, s.children, o), s = Fe.current, (s & 2) !== 0) s = s & 1 | 2, n.flags |= 128;
    else {
      if (e !== null && (e.flags & 128) !== 0) e: for (e = n.child; e !== null; ) {
        if (e.tag === 13) e.memoizedState !== null && id(e, o, n);
        else if (e.tag === 19) id(e, o, n);
        else if (e.child !== null) {
          e.child.return = e, e = e.child;
          continue;
        }
        if (e === n) break e;
        for (; e.sibling === null; ) {
          if (e.return === null || e.return === n) break e;
          e = e.return;
        }
        e.sibling.return = e.return, e = e.sibling;
      }
      s &= 1;
    }
    if (Le(Fe, s), (n.mode & 1) === 0) n.memoizedState = null;
    else switch (a) {
      case "forwards":
        for (o = n.child, a = null; o !== null; ) e = o.alternate, e !== null && cs(e) === null && (a = o), o = o.sibling;
        o = a, o === null ? (a = n.child, n.child = null) : (a = o.sibling, o.sibling = null), Xu(n, !1, a, o, d);
        break;
      case "backwards":
        for (o = null, a = n.child, n.child = null; a !== null; ) {
          if (e = a.alternate, e !== null && cs(e) === null) {
            n.child = a;
            break;
          }
          e = a.sibling, a.sibling = o, o = a, a = e;
        }
        Xu(n, !0, o, null, d);
        break;
      case "together":
        Xu(n, !1, null, null, void 0);
        break;
      default:
        n.memoizedState = null;
    }
    return n.child;
  }
  function vs(e, n) {
    (n.mode & 1) === 0 && e !== null && (e.alternate = null, n.alternate = null, n.flags |= 2);
  }
  function un(e, n, o) {
    if (e !== null && (n.dependencies = e.dependencies), tr |= n.lanes, (o & n.childLanes) === 0) return null;
    if (e !== null && n.child !== e.child) throw Error(i(153));
    if (n.child !== null) {
      for (e = n.child, o = Rn(e, e.pendingProps), n.child = o, o.return = n; e.sibling !== null; ) e = e.sibling, o = o.sibling = Rn(e, e.pendingProps), o.return = n;
      o.sibling = null;
    }
    return n.child;
  }
  function Zm(e, n, o) {
    switch (n.tag) {
      case 3:
        nd(n), Rr();
        break;
      case 5:
        xf(n);
        break;
      case 1:
        st(n.type) && es(n);
        break;
      case 4:
        Cu(n, n.stateNode.containerInfo);
        break;
      case 10:
        var s = n.type._context, a = n.memoizedProps.value;
        Le(ss, s._currentValue), s._currentValue = a;
        break;
      case 13:
        if (s = n.memoizedState, s !== null)
          return s.dehydrated !== null ? (Le(Fe, Fe.current & 1), n.flags |= 128, null) : (o & n.child.childLanes) !== 0 ? od(e, n, o) : (Le(Fe, Fe.current & 1), e = un(e, n, o), e !== null ? e.sibling : null);
        Le(Fe, Fe.current & 1);
        break;
      case 19:
        if (s = (o & n.childLanes) !== 0, (e.flags & 128) !== 0) {
          if (s) return sd(e, n, o);
          n.flags |= 128;
        }
        if (a = n.memoizedState, a !== null && (a.rendering = null, a.tail = null, a.lastEffect = null), Le(Fe, Fe.current), s) break;
        return null;
      case 22:
      case 23:
        return n.lanes = 0, Jf(e, n, o);
    }
    return un(e, n, o);
  }
  var ld, Qu, ud, ad;
  ld = function(e, n) {
    for (var o = n.child; o !== null; ) {
      if (o.tag === 5 || o.tag === 6) e.appendChild(o.stateNode);
      else if (o.tag !== 4 && o.child !== null) {
        o.child.return = o, o = o.child;
        continue;
      }
      if (o === n) break;
      for (; o.sibling === null; ) {
        if (o.return === null || o.return === n) return;
        o = o.return;
      }
      o.sibling.return = o.return, o = o.sibling;
    }
  }, Qu = function() {
  }, ud = function(e, n, o, s) {
    var a = e.memoizedProps;
    if (a !== s) {
      e = n.stateNode, Jn(Ut.current);
      var d = null;
      switch (o) {
        case "input":
          a = Ne(e, a), s = Ne(e, s), d = [];
          break;
        case "select":
          a = V({}, a, { value: void 0 }), s = V({}, s, { value: void 0 }), d = [];
          break;
        case "textarea":
          a = Zt(e, a), s = Zt(e, s), d = [];
          break;
        default:
          typeof a.onClick != "function" && typeof s.onClick == "function" && (e.onclick = Zi);
      }
      ro(o, s);
      var w;
      o = null;
      for (Z in a) if (!s.hasOwnProperty(Z) && a.hasOwnProperty(Z) && a[Z] != null) if (Z === "style") {
        var P = a[Z];
        for (w in P) P.hasOwnProperty(w) && (o || (o = {}), o[w] = "");
      } else Z !== "dangerouslySetInnerHTML" && Z !== "children" && Z !== "suppressContentEditableWarning" && Z !== "suppressHydrationWarning" && Z !== "autoFocus" && (u.hasOwnProperty(Z) ? d || (d = []) : (d = d || []).push(Z, null));
      for (Z in s) {
        var R = s[Z];
        if (P = a != null ? a[Z] : void 0, s.hasOwnProperty(Z) && R !== P && (R != null || P != null)) if (Z === "style") if (P) {
          for (w in P) !P.hasOwnProperty(w) || R && R.hasOwnProperty(w) || (o || (o = {}), o[w] = "");
          for (w in R) R.hasOwnProperty(w) && P[w] !== R[w] && (o || (o = {}), o[w] = R[w]);
        } else o || (d || (d = []), d.push(
          Z,
          o
        )), o = R;
        else Z === "dangerouslySetInnerHTML" ? (R = R ? R.__html : void 0, P = P ? P.__html : void 0, R != null && P !== R && (d = d || []).push(Z, R)) : Z === "children" ? typeof R != "string" && typeof R != "number" || (d = d || []).push(Z, "" + R) : Z !== "suppressContentEditableWarning" && Z !== "suppressHydrationWarning" && (u.hasOwnProperty(Z) ? (R != null && Z === "onScroll" && Ae("scroll", e), d || P === R || (d = [])) : (d = d || []).push(Z, R));
      }
      o && (d = d || []).push("style", o);
      var Z = d;
      (n.updateQueue = Z) && (n.flags |= 4);
    }
  }, ad = function(e, n, o, s) {
    o !== s && (n.flags |= 4);
  };
  function Bo(e, n) {
    if (!De) switch (e.tailMode) {
      case "hidden":
        n = e.tail;
        for (var o = null; n !== null; ) n.alternate !== null && (o = n), n = n.sibling;
        o === null ? e.tail = null : o.sibling = null;
        break;
      case "collapsed":
        o = e.tail;
        for (var s = null; o !== null; ) o.alternate !== null && (s = o), o = o.sibling;
        s === null ? n || e.tail === null ? e.tail = null : e.tail.sibling = null : s.sibling = null;
    }
  }
  function tt(e) {
    var n = e.alternate !== null && e.alternate.child === e.child, o = 0, s = 0;
    if (n) for (var a = e.child; a !== null; ) o |= a.lanes | a.childLanes, s |= a.subtreeFlags & 14680064, s |= a.flags & 14680064, a.return = e, a = a.sibling;
    else for (a = e.child; a !== null; ) o |= a.lanes | a.childLanes, s |= a.subtreeFlags, s |= a.flags, a.return = e, a = a.sibling;
    return e.subtreeFlags |= s, e.childLanes = o, n;
  }
  function qm(e, n, o) {
    var s = n.pendingProps;
    switch (mu(n), n.tag) {
      case 2:
      case 16:
      case 15:
      case 0:
      case 11:
      case 7:
      case 8:
      case 12:
      case 9:
      case 14:
        return tt(n), null;
      case 1:
        return st(n.type) && Ji(), tt(n), null;
      case 3:
        return s = n.stateNode, Or(), Re(it), Re(Je), Iu(), s.pendingContext && (s.context = s.pendingContext, s.pendingContext = null), (e === null || e.child === null) && (os(n) ? n.flags |= 4 : e === null || e.memoizedState.isDehydrated && (n.flags & 256) === 0 || (n.flags |= 1024, Tt !== null && (ia(Tt), Tt = null))), Qu(e, n), tt(n), null;
      case 5:
        Mu(n);
        var a = Jn(jo.current);
        if (o = n.type, e !== null && n.stateNode != null) ud(e, n, o, s, a), e.ref !== n.ref && (n.flags |= 512, n.flags |= 2097152);
        else {
          if (!s) {
            if (n.stateNode === null) throw Error(i(166));
            return tt(n), null;
          }
          if (e = Jn(Ut.current), os(n)) {
            s = n.stateNode, o = n.type;
            var d = n.memoizedProps;
            switch (s[Wt] = n, s[Lo] = d, e = (n.mode & 1) !== 0, o) {
              case "dialog":
                Ae("cancel", s), Ae("close", s);
                break;
              case "iframe":
              case "object":
              case "embed":
                Ae("load", s);
                break;
              case "video":
              case "audio":
                for (a = 0; a < Io.length; a++) Ae(Io[a], s);
                break;
              case "source":
                Ae("error", s);
                break;
              case "img":
              case "image":
              case "link":
                Ae(
                  "error",
                  s
                ), Ae("load", s);
                break;
              case "details":
                Ae("toggle", s);
                break;
              case "input":
                Te(s, d), Ae("invalid", s);
                break;
              case "select":
                s._wrapperState = { wasMultiple: !!d.multiple }, Ae("invalid", s);
                break;
              case "textarea":
                pn(s, d), Ae("invalid", s);
            }
            ro(o, d), a = null;
            for (var w in d) if (d.hasOwnProperty(w)) {
              var P = d[w];
              w === "children" ? typeof P == "string" ? s.textContent !== P && (d.suppressHydrationWarning !== !0 && Gi(s.textContent, P, e), a = ["children", P]) : typeof P == "number" && s.textContent !== "" + P && (d.suppressHydrationWarning !== !0 && Gi(
                s.textContent,
                P,
                e
              ), a = ["children", "" + P]) : u.hasOwnProperty(w) && P != null && w === "onScroll" && Ae("scroll", s);
            }
            switch (o) {
              case "input":
                _e(s), It(s, d, !0);
                break;
              case "textarea":
                _e(s), Hn(s);
                break;
              case "select":
              case "option":
                break;
              default:
                typeof d.onClick == "function" && (s.onclick = Zi);
            }
            s = a, n.updateQueue = s, s !== null && (n.flags |= 4);
          } else {
            w = a.nodeType === 9 ? a : a.ownerDocument, e === "http://www.w3.org/1999/xhtml" && (e = qt(o)), e === "http://www.w3.org/1999/xhtml" ? o === "script" ? (e = w.createElement("div"), e.innerHTML = "<script><\/script>", e = e.removeChild(e.firstChild)) : typeof s.is == "string" ? e = w.createElement(o, { is: s.is }) : (e = w.createElement(o), o === "select" && (w = e, s.multiple ? w.multiple = !0 : s.size && (w.size = s.size))) : e = w.createElementNS(e, o), e[Wt] = n, e[Lo] = s, ld(e, n, !1, !1), n.stateNode = e;
            e: {
              switch (w = oo(o, s), o) {
                case "dialog":
                  Ae("cancel", e), Ae("close", e), a = s;
                  break;
                case "iframe":
                case "object":
                case "embed":
                  Ae("load", e), a = s;
                  break;
                case "video":
                case "audio":
                  for (a = 0; a < Io.length; a++) Ae(Io[a], e);
                  a = s;
                  break;
                case "source":
                  Ae("error", e), a = s;
                  break;
                case "img":
                case "image":
                case "link":
                  Ae(
                    "error",
                    e
                  ), Ae("load", e), a = s;
                  break;
                case "details":
                  Ae("toggle", e), a = s;
                  break;
                case "input":
                  Te(e, s), a = Ne(e, s), Ae("invalid", e);
                  break;
                case "option":
                  a = s;
                  break;
                case "select":
                  e._wrapperState = { wasMultiple: !!s.multiple }, a = V({}, s, { value: void 0 }), Ae("invalid", e);
                  break;
                case "textarea":
                  pn(e, s), a = Zt(e, s), Ae("invalid", e);
                  break;
                default:
                  a = s;
              }
              ro(o, a), P = a;
              for (d in P) if (P.hasOwnProperty(d)) {
                var R = P[d];
                d === "style" ? Si(e, R) : d === "dangerouslySetInnerHTML" ? (R = R ? R.__html : void 0, R != null && wi(e, R)) : d === "children" ? typeof R == "string" ? (o !== "textarea" || R !== "") && en(e, R) : typeof R == "number" && en(e, "" + R) : d !== "suppressContentEditableWarning" && d !== "suppressHydrationWarning" && d !== "autoFocus" && (u.hasOwnProperty(d) ? R != null && d === "onScroll" && Ae("scroll", e) : R != null && _(e, d, R, w));
              }
              switch (o) {
                case "input":
                  _e(e), It(e, s, !1);
                  break;
                case "textarea":
                  _e(e), Hn(e);
                  break;
                case "option":
                  s.value != null && e.setAttribute("value", "" + J(s.value));
                  break;
                case "select":
                  e.multiple = !!s.multiple, d = s.value, d != null ? St(e, !!s.multiple, d, !1) : s.defaultValue != null && St(
                    e,
                    !!s.multiple,
                    s.defaultValue,
                    !0
                  );
                  break;
                default:
                  typeof a.onClick == "function" && (e.onclick = Zi);
              }
              switch (o) {
                case "button":
                case "input":
                case "select":
                case "textarea":
                  s = !!s.autoFocus;
                  break e;
                case "img":
                  s = !0;
                  break e;
                default:
                  s = !1;
              }
            }
            s && (n.flags |= 4);
          }
          n.ref !== null && (n.flags |= 512, n.flags |= 2097152);
        }
        return tt(n), null;
      case 6:
        if (e && n.stateNode != null) ad(e, n, e.memoizedProps, s);
        else {
          if (typeof s != "string" && n.stateNode === null) throw Error(i(166));
          if (o = Jn(jo.current), Jn(Ut.current), os(n)) {
            if (s = n.stateNode, o = n.memoizedProps, s[Wt] = n, (d = s.nodeValue !== o) && (e = mt, e !== null)) switch (e.tag) {
              case 3:
                Gi(s.nodeValue, o, (e.mode & 1) !== 0);
                break;
              case 5:
                e.memoizedProps.suppressHydrationWarning !== !0 && Gi(s.nodeValue, o, (e.mode & 1) !== 0);
            }
            d && (n.flags |= 4);
          } else s = (o.nodeType === 9 ? o : o.ownerDocument).createTextNode(s), s[Wt] = n, n.stateNode = s;
        }
        return tt(n), null;
      case 13:
        if (Re(Fe), s = n.memoizedState, e === null || e.memoizedState !== null && e.memoizedState.dehydrated !== null) {
          if (De && yt !== null && (n.mode & 1) !== 0 && (n.flags & 128) === 0) df(), Rr(), n.flags |= 98560, d = !1;
          else if (d = os(n), s !== null && s.dehydrated !== null) {
            if (e === null) {
              if (!d) throw Error(i(318));
              if (d = n.memoizedState, d = d !== null ? d.dehydrated : null, !d) throw Error(i(317));
              d[Wt] = n;
            } else Rr(), (n.flags & 128) === 0 && (n.memoizedState = null), n.flags |= 4;
            tt(n), d = !1;
          } else Tt !== null && (ia(Tt), Tt = null), d = !0;
          if (!d) return n.flags & 65536 ? n : null;
        }
        return (n.flags & 128) !== 0 ? (n.lanes = o, n) : (s = s !== null, s !== (e !== null && e.memoizedState !== null) && s && (n.child.flags |= 8192, (n.mode & 1) !== 0 && (e === null || (Fe.current & 1) !== 0 ? Xe === 0 && (Xe = 3) : ua())), n.updateQueue !== null && (n.flags |= 4), tt(n), null);
      case 4:
        return Or(), Qu(e, n), e === null && zo(n.stateNode.containerInfo), tt(n), null;
      case 10:
        return _u(n.type._context), tt(n), null;
      case 17:
        return st(n.type) && Ji(), tt(n), null;
      case 19:
        if (Re(Fe), d = n.memoizedState, d === null) return tt(n), null;
        if (s = (n.flags & 128) !== 0, w = d.rendering, w === null) if (s) Bo(d, !1);
        else {
          if (Xe !== 0 || e !== null && (e.flags & 128) !== 0) for (e = n.child; e !== null; ) {
            if (w = cs(e), w !== null) {
              for (n.flags |= 128, Bo(d, !1), s = w.updateQueue, s !== null && (n.updateQueue = s, n.flags |= 4), n.subtreeFlags = 0, s = o, o = n.child; o !== null; ) d = o, e = s, d.flags &= 14680066, w = d.alternate, w === null ? (d.childLanes = 0, d.lanes = e, d.child = null, d.subtreeFlags = 0, d.memoizedProps = null, d.memoizedState = null, d.updateQueue = null, d.dependencies = null, d.stateNode = null) : (d.childLanes = w.childLanes, d.lanes = w.lanes, d.child = w.child, d.subtreeFlags = 0, d.deletions = null, d.memoizedProps = w.memoizedProps, d.memoizedState = w.memoizedState, d.updateQueue = w.updateQueue, d.type = w.type, e = w.dependencies, d.dependencies = e === null ? null : { lanes: e.lanes, firstContext: e.firstContext }), o = o.sibling;
              return Le(Fe, Fe.current & 1 | 2), n.child;
            }
            e = e.sibling;
          }
          d.tail !== null && Oe() > Br && (n.flags |= 128, s = !0, Bo(d, !1), n.lanes = 4194304);
        }
        else {
          if (!s) if (e = cs(w), e !== null) {
            if (n.flags |= 128, s = !0, o = e.updateQueue, o !== null && (n.updateQueue = o, n.flags |= 4), Bo(d, !0), d.tail === null && d.tailMode === "hidden" && !w.alternate && !De) return tt(n), null;
          } else 2 * Oe() - d.renderingStartTime > Br && o !== 1073741824 && (n.flags |= 128, s = !0, Bo(d, !1), n.lanes = 4194304);
          d.isBackwards ? (w.sibling = n.child, n.child = w) : (o = d.last, o !== null ? o.sibling = w : n.child = w, d.last = w);
        }
        return d.tail !== null ? (n = d.tail, d.rendering = n, d.tail = n.sibling, d.renderingStartTime = Oe(), n.sibling = null, o = Fe.current, Le(Fe, s ? o & 1 | 2 : o & 1), n) : (tt(n), null);
      case 22:
      case 23:
        return la(), s = n.memoizedState !== null, e !== null && e.memoizedState !== null !== s && (n.flags |= 8192), s && (n.mode & 1) !== 0 ? (vt & 1073741824) !== 0 && (tt(n), n.subtreeFlags & 6 && (n.flags |= 8192)) : tt(n), null;
      case 24:
        return null;
      case 25:
        return null;
    }
    throw Error(i(156, n.tag));
  }
  function Jm(e, n) {
    switch (mu(n), n.tag) {
      case 1:
        return st(n.type) && Ji(), e = n.flags, e & 65536 ? (n.flags = e & -65537 | 128, n) : null;
      case 3:
        return Or(), Re(it), Re(Je), Iu(), e = n.flags, (e & 65536) !== 0 && (e & 128) === 0 ? (n.flags = e & -65537 | 128, n) : null;
      case 5:
        return Mu(n), null;
      case 13:
        if (Re(Fe), e = n.memoizedState, e !== null && e.dehydrated !== null) {
          if (n.alternate === null) throw Error(i(340));
          Rr();
        }
        return e = n.flags, e & 65536 ? (n.flags = e & -65537 | 128, n) : null;
      case 19:
        return Re(Fe), null;
      case 4:
        return Or(), null;
      case 10:
        return _u(n.type._context), null;
      case 22:
      case 23:
        return la(), null;
      case 24:
        return null;
      default:
        return null;
    }
  }
  var ws = !1, nt = !1, e0 = typeof WeakSet == "function" ? WeakSet : Set, de = null;
  function Hr(e, n) {
    var o = e.ref;
    if (o !== null) if (typeof o == "function") try {
      o(null);
    } catch (s) {
      Ve(e, n, s);
    }
    else o.current = null;
  }
  function Ku(e, n, o) {
    try {
      o();
    } catch (s) {
      Ve(e, n, s);
    }
  }
  var cd = !1;
  function t0(e, n) {
    if (lu = Fi, e = Vc(), Jl(e)) {
      if ("selectionStart" in e) var o = { start: e.selectionStart, end: e.selectionEnd };
      else e: {
        o = (o = e.ownerDocument) && o.defaultView || window;
        var s = o.getSelection && o.getSelection();
        if (s && s.rangeCount !== 0) {
          o = s.anchorNode;
          var a = s.anchorOffset, d = s.focusNode;
          s = s.focusOffset;
          try {
            o.nodeType, d.nodeType;
          } catch {
            o = null;
            break e;
          }
          var w = 0, P = -1, R = -1, Z = 0, oe = 0, ie = e, ne = null;
          t: for (; ; ) {
            for (var fe; ie !== o || a !== 0 && ie.nodeType !== 3 || (P = w + a), ie !== d || s !== 0 && ie.nodeType !== 3 || (R = w + s), ie.nodeType === 3 && (w += ie.nodeValue.length), (fe = ie.firstChild) !== null; )
              ne = ie, ie = fe;
            for (; ; ) {
              if (ie === e) break t;
              if (ne === o && ++Z === a && (P = w), ne === d && ++oe === s && (R = w), (fe = ie.nextSibling) !== null) break;
              ie = ne, ne = ie.parentNode;
            }
            ie = fe;
          }
          o = P === -1 || R === -1 ? null : { start: P, end: R };
        } else o = null;
      }
      o = o || { start: 0, end: 0 };
    } else o = null;
    for (uu = { focusedElem: e, selectionRange: o }, Fi = !1, de = n; de !== null; ) if (n = de, e = n.child, (n.subtreeFlags & 1028) !== 0 && e !== null) e.return = n, de = e;
    else for (; de !== null; ) {
      n = de;
      try {
        var he = n.alternate;
        if ((n.flags & 1024) !== 0) switch (n.tag) {
          case 0:
          case 11:
          case 15:
            break;
          case 1:
            if (he !== null) {
              var pe = he.memoizedProps, Be = he.memoizedState, Y = n.stateNode, O = Y.getSnapshotBeforeUpdate(n.elementType === n.type ? pe : Lt(n.type, pe), Be);
              Y.__reactInternalSnapshotBeforeUpdate = O;
            }
            break;
          case 3:
            var X = n.stateNode.containerInfo;
            X.nodeType === 1 ? X.textContent = "" : X.nodeType === 9 && X.documentElement && X.removeChild(X.documentElement);
            break;
          case 5:
          case 6:
          case 4:
          case 17:
            break;
          default:
            throw Error(i(163));
        }
      } catch (se) {
        Ve(n, n.return, se);
      }
      if (e = n.sibling, e !== null) {
        e.return = n.return, de = e;
        break;
      }
      de = n.return;
    }
    return he = cd, cd = !1, he;
  }
  function Wo(e, n, o) {
    var s = n.updateQueue;
    if (s = s !== null ? s.lastEffect : null, s !== null) {
      var a = s = s.next;
      do {
        if ((a.tag & e) === e) {
          var d = a.destroy;
          a.destroy = void 0, d !== void 0 && Ku(n, o, d);
        }
        a = a.next;
      } while (a !== s);
    }
  }
  function xs(e, n) {
    if (n = n.updateQueue, n = n !== null ? n.lastEffect : null, n !== null) {
      var o = n = n.next;
      do {
        if ((o.tag & e) === e) {
          var s = o.create;
          o.destroy = s();
        }
        o = o.next;
      } while (o !== n);
    }
  }
  function Gu(e) {
    var n = e.ref;
    if (n !== null) {
      var o = e.stateNode;
      switch (e.tag) {
        case 5:
          e = o;
          break;
        default:
          e = o;
      }
      typeof n == "function" ? n(e) : n.current = e;
    }
  }
  function fd(e) {
    var n = e.alternate;
    n !== null && (e.alternate = null, fd(n)), e.child = null, e.deletions = null, e.sibling = null, e.tag === 5 && (n = e.stateNode, n !== null && (delete n[Wt], delete n[Lo], delete n[du], delete n[jm], delete n[Om])), e.stateNode = null, e.return = null, e.dependencies = null, e.memoizedProps = null, e.memoizedState = null, e.pendingProps = null, e.stateNode = null, e.updateQueue = null;
  }
  function dd(e) {
    return e.tag === 5 || e.tag === 3 || e.tag === 4;
  }
  function hd(e) {
    e: for (; ; ) {
      for (; e.sibling === null; ) {
        if (e.return === null || dd(e.return)) return null;
        e = e.return;
      }
      for (e.sibling.return = e.return, e = e.sibling; e.tag !== 5 && e.tag !== 6 && e.tag !== 18; ) {
        if (e.flags & 2 || e.child === null || e.tag === 4) continue e;
        e.child.return = e, e = e.child;
      }
      if (!(e.flags & 2)) return e.stateNode;
    }
  }
  function Zu(e, n, o) {
    var s = e.tag;
    if (s === 5 || s === 6) e = e.stateNode, n ? o.nodeType === 8 ? o.parentNode.insertBefore(e, n) : o.insertBefore(e, n) : (o.nodeType === 8 ? (n = o.parentNode, n.insertBefore(e, o)) : (n = o, n.appendChild(e)), o = o._reactRootContainer, o != null || n.onclick !== null || (n.onclick = Zi));
    else if (s !== 4 && (e = e.child, e !== null)) for (Zu(e, n, o), e = e.sibling; e !== null; ) Zu(e, n, o), e = e.sibling;
  }
  function qu(e, n, o) {
    var s = e.tag;
    if (s === 5 || s === 6) e = e.stateNode, n ? o.insertBefore(e, n) : o.appendChild(e);
    else if (s !== 4 && (e = e.child, e !== null)) for (qu(e, n, o), e = e.sibling; e !== null; ) qu(e, n, o), e = e.sibling;
  }
  var Ze = null, At = !1;
  function In(e, n, o) {
    for (o = o.child; o !== null; ) pd(e, n, o), o = o.sibling;
  }
  function pd(e, n, o) {
    if (_t && typeof _t.onCommitFiberUnmount == "function") try {
      _t.onCommitFiberUnmount(bn, o);
    } catch {
    }
    switch (o.tag) {
      case 5:
        nt || Hr(o, n);
      case 6:
        var s = Ze, a = At;
        Ze = null, In(e, n, o), Ze = s, At = a, Ze !== null && (At ? (e = Ze, o = o.stateNode, e.nodeType === 8 ? e.parentNode.removeChild(o) : e.removeChild(o)) : Ze.removeChild(o.stateNode));
        break;
      case 18:
        Ze !== null && (At ? (e = Ze, o = o.stateNode, e.nodeType === 8 ? fu(e.parentNode, o) : e.nodeType === 1 && fu(e, o), So(e)) : fu(Ze, o.stateNode));
        break;
      case 4:
        s = Ze, a = At, Ze = o.stateNode.containerInfo, At = !0, In(e, n, o), Ze = s, At = a;
        break;
      case 0:
      case 11:
      case 14:
      case 15:
        if (!nt && (s = o.updateQueue, s !== null && (s = s.lastEffect, s !== null))) {
          a = s = s.next;
          do {
            var d = a, w = d.destroy;
            d = d.tag, w !== void 0 && ((d & 2) !== 0 || (d & 4) !== 0) && Ku(o, n, w), a = a.next;
          } while (a !== s);
        }
        In(e, n, o);
        break;
      case 1:
        if (!nt && (Hr(o, n), s = o.stateNode, typeof s.componentWillUnmount == "function")) try {
          s.props = o.memoizedProps, s.state = o.memoizedState, s.componentWillUnmount();
        } catch (P) {
          Ve(o, n, P);
        }
        In(e, n, o);
        break;
      case 21:
        In(e, n, o);
        break;
      case 22:
        o.mode & 1 ? (nt = (s = nt) || o.memoizedState !== null, In(e, n, o), nt = s) : In(e, n, o);
        break;
      default:
        In(e, n, o);
    }
  }
  function gd(e) {
    var n = e.updateQueue;
    if (n !== null) {
      e.updateQueue = null;
      var o = e.stateNode;
      o === null && (o = e.stateNode = new e0()), n.forEach(function(s) {
        var a = c0.bind(null, e, s);
        o.has(s) || (o.add(s), s.then(a, a));
      });
    }
  }
  function Rt(e, n) {
    var o = n.deletions;
    if (o !== null) for (var s = 0; s < o.length; s++) {
      var a = o[s];
      try {
        var d = e, w = n, P = w;
        e: for (; P !== null; ) {
          switch (P.tag) {
            case 5:
              Ze = P.stateNode, At = !1;
              break e;
            case 3:
              Ze = P.stateNode.containerInfo, At = !0;
              break e;
            case 4:
              Ze = P.stateNode.containerInfo, At = !0;
              break e;
          }
          P = P.return;
        }
        if (Ze === null) throw Error(i(160));
        pd(d, w, a), Ze = null, At = !1;
        var R = a.alternate;
        R !== null && (R.return = null), a.return = null;
      } catch (Z) {
        Ve(a, n, Z);
      }
    }
    if (n.subtreeFlags & 12854) for (n = n.child; n !== null; ) md(n, e), n = n.sibling;
  }
  function md(e, n) {
    var o = e.alternate, s = e.flags;
    switch (e.tag) {
      case 0:
      case 11:
      case 14:
      case 15:
        if (Rt(n, e), bt(e), s & 4) {
          try {
            Wo(3, e, e.return), xs(3, e);
          } catch (pe) {
            Ve(e, e.return, pe);
          }
          try {
            Wo(5, e, e.return);
          } catch (pe) {
            Ve(e, e.return, pe);
          }
        }
        break;
      case 1:
        Rt(n, e), bt(e), s & 512 && o !== null && Hr(o, o.return);
        break;
      case 5:
        if (Rt(n, e), bt(e), s & 512 && o !== null && Hr(o, o.return), e.flags & 32) {
          var a = e.stateNode;
          try {
            en(a, "");
          } catch (pe) {
            Ve(e, e.return, pe);
          }
        }
        if (s & 4 && (a = e.stateNode, a != null)) {
          var d = e.memoizedProps, w = o !== null ? o.memoizedProps : d, P = e.type, R = e.updateQueue;
          if (e.updateQueue = null, R !== null) try {
            P === "input" && d.type === "radio" && d.name != null && Ce(a, d), oo(P, w);
            var Z = oo(P, d);
            for (w = 0; w < R.length; w += 2) {
              var oe = R[w], ie = R[w + 1];
              oe === "style" ? Si(a, ie) : oe === "dangerouslySetInnerHTML" ? wi(a, ie) : oe === "children" ? en(a, ie) : _(a, oe, ie, Z);
            }
            switch (P) {
              case "input":
                We(a, d);
                break;
              case "textarea":
                pr(a, d);
                break;
              case "select":
                var ne = a._wrapperState.wasMultiple;
                a._wrapperState.wasMultiple = !!d.multiple;
                var fe = d.value;
                fe != null ? St(a, !!d.multiple, fe, !1) : ne !== !!d.multiple && (d.defaultValue != null ? St(
                  a,
                  !!d.multiple,
                  d.defaultValue,
                  !0
                ) : St(a, !!d.multiple, d.multiple ? [] : "", !1));
            }
            a[Lo] = d;
          } catch (pe) {
            Ve(e, e.return, pe);
          }
        }
        break;
      case 6:
        if (Rt(n, e), bt(e), s & 4) {
          if (e.stateNode === null) throw Error(i(162));
          a = e.stateNode, d = e.memoizedProps;
          try {
            a.nodeValue = d;
          } catch (pe) {
            Ve(e, e.return, pe);
          }
        }
        break;
      case 3:
        if (Rt(n, e), bt(e), s & 4 && o !== null && o.memoizedState.isDehydrated) try {
          So(n.containerInfo);
        } catch (pe) {
          Ve(e, e.return, pe);
        }
        break;
      case 4:
        Rt(n, e), bt(e);
        break;
      case 13:
        Rt(n, e), bt(e), a = e.child, a.flags & 8192 && (d = a.memoizedState !== null, a.stateNode.isHidden = d, !d || a.alternate !== null && a.alternate.memoizedState !== null || (ta = Oe())), s & 4 && gd(e);
        break;
      case 22:
        if (oe = o !== null && o.memoizedState !== null, e.mode & 1 ? (nt = (Z = nt) || oe, Rt(n, e), nt = Z) : Rt(n, e), bt(e), s & 8192) {
          if (Z = e.memoizedState !== null, (e.stateNode.isHidden = Z) && !oe && (e.mode & 1) !== 0) for (de = e, oe = e.child; oe !== null; ) {
            for (ie = de = oe; de !== null; ) {
              switch (ne = de, fe = ne.child, ne.tag) {
                case 0:
                case 11:
                case 14:
                case 15:
                  Wo(4, ne, ne.return);
                  break;
                case 1:
                  Hr(ne, ne.return);
                  var he = ne.stateNode;
                  if (typeof he.componentWillUnmount == "function") {
                    s = ne, o = ne.return;
                    try {
                      n = s, he.props = n.memoizedProps, he.state = n.memoizedState, he.componentWillUnmount();
                    } catch (pe) {
                      Ve(s, o, pe);
                    }
                  }
                  break;
                case 5:
                  Hr(ne, ne.return);
                  break;
                case 22:
                  if (ne.memoizedState !== null) {
                    wd(ie);
                    continue;
                  }
              }
              fe !== null ? (fe.return = ne, de = fe) : wd(ie);
            }
            oe = oe.sibling;
          }
          e: for (oe = null, ie = e; ; ) {
            if (ie.tag === 5) {
              if (oe === null) {
                oe = ie;
                try {
                  a = ie.stateNode, Z ? (d = a.style, typeof d.setProperty == "function" ? d.setProperty("display", "none", "important") : d.display = "none") : (P = ie.stateNode, R = ie.memoizedProps.style, w = R != null && R.hasOwnProperty("display") ? R.display : null, P.style.display = xi("display", w));
                } catch (pe) {
                  Ve(e, e.return, pe);
                }
              }
            } else if (ie.tag === 6) {
              if (oe === null) try {
                ie.stateNode.nodeValue = Z ? "" : ie.memoizedProps;
              } catch (pe) {
                Ve(e, e.return, pe);
              }
            } else if ((ie.tag !== 22 && ie.tag !== 23 || ie.memoizedState === null || ie === e) && ie.child !== null) {
              ie.child.return = ie, ie = ie.child;
              continue;
            }
            if (ie === e) break e;
            for (; ie.sibling === null; ) {
              if (ie.return === null || ie.return === e) break e;
              oe === ie && (oe = null), ie = ie.return;
            }
            oe === ie && (oe = null), ie.sibling.return = ie.return, ie = ie.sibling;
          }
        }
        break;
      case 19:
        Rt(n, e), bt(e), s & 4 && gd(e);
        break;
      case 21:
        break;
      default:
        Rt(
          n,
          e
        ), bt(e);
    }
  }
  function bt(e) {
    var n = e.flags;
    if (n & 2) {
      try {
        e: {
          for (var o = e.return; o !== null; ) {
            if (dd(o)) {
              var s = o;
              break e;
            }
            o = o.return;
          }
          throw Error(i(160));
        }
        switch (s.tag) {
          case 5:
            var a = s.stateNode;
            s.flags & 32 && (en(a, ""), s.flags &= -33);
            var d = hd(e);
            qu(e, d, a);
            break;
          case 3:
          case 4:
            var w = s.stateNode.containerInfo, P = hd(e);
            Zu(e, P, w);
            break;
          default:
            throw Error(i(161));
        }
      } catch (R) {
        Ve(e, e.return, R);
      }
      e.flags &= -3;
    }
    n & 4096 && (e.flags &= -4097);
  }
  function n0(e, n, o) {
    de = e, yd(e);
  }
  function yd(e, n, o) {
    for (var s = (e.mode & 1) !== 0; de !== null; ) {
      var a = de, d = a.child;
      if (a.tag === 22 && s) {
        var w = a.memoizedState !== null || ws;
        if (!w) {
          var P = a.alternate, R = P !== null && P.memoizedState !== null || nt;
          P = ws;
          var Z = nt;
          if (ws = w, (nt = R) && !Z) for (de = a; de !== null; ) w = de, R = w.child, w.tag === 22 && w.memoizedState !== null ? xd(a) : R !== null ? (R.return = w, de = R) : xd(a);
          for (; d !== null; ) de = d, yd(d), d = d.sibling;
          de = a, ws = P, nt = Z;
        }
        vd(e);
      } else (a.subtreeFlags & 8772) !== 0 && d !== null ? (d.return = a, de = d) : vd(e);
    }
  }
  function vd(e) {
    for (; de !== null; ) {
      var n = de;
      if ((n.flags & 8772) !== 0) {
        var o = n.alternate;
        try {
          if ((n.flags & 8772) !== 0) switch (n.tag) {
            case 0:
            case 11:
            case 15:
              nt || xs(5, n);
              break;
            case 1:
              var s = n.stateNode;
              if (n.flags & 4 && !nt) if (o === null) s.componentDidMount();
              else {
                var a = n.elementType === n.type ? o.memoizedProps : Lt(n.type, o.memoizedProps);
                s.componentDidUpdate(a, o.memoizedState, s.__reactInternalSnapshotBeforeUpdate);
              }
              var d = n.updateQueue;
              d !== null && wf(n, d, s);
              break;
            case 3:
              var w = n.updateQueue;
              if (w !== null) {
                if (o = null, n.child !== null) switch (n.child.tag) {
                  case 5:
                    o = n.child.stateNode;
                    break;
                  case 1:
                    o = n.child.stateNode;
                }
                wf(n, w, o);
              }
              break;
            case 5:
              var P = n.stateNode;
              if (o === null && n.flags & 4) {
                o = P;
                var R = n.memoizedProps;
                switch (n.type) {
                  case "button":
                  case "input":
                  case "select":
                  case "textarea":
                    R.autoFocus && o.focus();
                    break;
                  case "img":
                    R.src && (o.src = R.src);
                }
              }
              break;
            case 6:
              break;
            case 4:
              break;
            case 12:
              break;
            case 13:
              if (n.memoizedState === null) {
                var Z = n.alternate;
                if (Z !== null) {
                  var oe = Z.memoizedState;
                  if (oe !== null) {
                    var ie = oe.dehydrated;
                    ie !== null && So(ie);
                  }
                }
              }
              break;
            case 19:
            case 17:
            case 21:
            case 22:
            case 23:
            case 25:
              break;
            default:
              throw Error(i(163));
          }
          nt || n.flags & 512 && Gu(n);
        } catch (ne) {
          Ve(n, n.return, ne);
        }
      }
      if (n === e) {
        de = null;
        break;
      }
      if (o = n.sibling, o !== null) {
        o.return = n.return, de = o;
        break;
      }
      de = n.return;
    }
  }
  function wd(e) {
    for (; de !== null; ) {
      var n = de;
      if (n === e) {
        de = null;
        break;
      }
      var o = n.sibling;
      if (o !== null) {
        o.return = n.return, de = o;
        break;
      }
      de = n.return;
    }
  }
  function xd(e) {
    for (; de !== null; ) {
      var n = de;
      try {
        switch (n.tag) {
          case 0:
          case 11:
          case 15:
            var o = n.return;
            try {
              xs(4, n);
            } catch (R) {
              Ve(n, o, R);
            }
            break;
          case 1:
            var s = n.stateNode;
            if (typeof s.componentDidMount == "function") {
              var a = n.return;
              try {
                s.componentDidMount();
              } catch (R) {
                Ve(n, a, R);
              }
            }
            var d = n.return;
            try {
              Gu(n);
            } catch (R) {
              Ve(n, d, R);
            }
            break;
          case 5:
            var w = n.return;
            try {
              Gu(n);
            } catch (R) {
              Ve(n, w, R);
            }
        }
      } catch (R) {
        Ve(n, n.return, R);
      }
      if (n === e) {
        de = null;
        break;
      }
      var P = n.sibling;
      if (P !== null) {
        P.return = n.return, de = P;
        break;
      }
      de = n.return;
    }
  }
  var r0 = Math.ceil, Ss = E.ReactCurrentDispatcher, Ju = E.ReactCurrentOwner, Mt = E.ReactCurrentBatchConfig, Pe = 0, Ke = null, Ue = null, qe = 0, vt = 0, Vr = kn(0), Xe = 0, Uo = null, tr = 0, _s = 0, ea = 0, Yo = null, ut = null, ta = 0, Br = 1 / 0, an = null, Es = !1, na = null, zn = null, ks = !1, Tn = null, Ns = 0, bo = 0, ra = null, Cs = -1, Ms = 0;
  function ot() {
    return (Pe & 6) !== 0 ? Oe() : Cs !== -1 ? Cs : Cs = Oe();
  }
  function Ln(e) {
    return (e.mode & 1) === 0 ? 1 : (Pe & 2) !== 0 && qe !== 0 ? qe & -qe : Hm.transition !== null ? (Ms === 0 && (Ms = Sr()), Ms) : (e = ze, e !== 0 || (e = window.event, e = e === void 0 ? 16 : Sc(e.type)), e);
  }
  function $t(e, n, o, s) {
    if (50 < bo) throw bo = 0, ra = null, Error(i(185));
    Xn(e, o, s), ((Pe & 2) === 0 || e !== Ke) && (e === Ke && ((Pe & 2) === 0 && (_s |= o), Xe === 4 && An(e, qe)), at(e, s), o === 1 && Pe === 0 && (n.mode & 1) === 0 && (Br = Oe() + 500, ts && Cn()));
  }
  function at(e, n) {
    var o = e.callbackNode;
    Ol(e, n);
    var s = xr(e, e === Ke ? qe : 0);
    if (s === 0) o !== null && Ti(o), e.callbackNode = null, e.callbackPriority = 0;
    else if (n = s & -s, e.callbackPriority !== n) {
      if (o != null && Ti(o), n === 1) e.tag === 0 ? Fm(_d.bind(null, e)) : lf(_d.bind(null, e)), $m(function() {
        (Pe & 6) === 0 && Cn();
      }), o = null;
      else {
        switch (hc(s)) {
          case 1:
            o = po;
            break;
          case 4:
            o = Ai;
            break;
          case 16:
            o = yr;
            break;
          case 536870912:
            o = Ri;
            break;
          default:
            o = yr;
        }
        o = zd(o, Sd.bind(null, e));
      }
      e.callbackPriority = n, e.callbackNode = o;
    }
  }
  function Sd(e, n) {
    if (Cs = -1, Ms = 0, (Pe & 6) !== 0) throw Error(i(327));
    var o = e.callbackNode;
    if (Wr() && e.callbackNode !== o) return null;
    var s = xr(e, e === Ke ? qe : 0);
    if (s === 0) return null;
    if ((s & 30) !== 0 || (s & e.expiredLanes) !== 0 || n) n = Ps(e, s);
    else {
      n = s;
      var a = Pe;
      Pe |= 2;
      var d = kd();
      (Ke !== e || qe !== n) && (an = null, Br = Oe() + 500, rr(e, n));
      do
        try {
          s0();
          break;
        } catch (P) {
          Ed(e, P);
        }
      while (!0);
      Su(), Ss.current = d, Pe = a, Ue !== null ? n = 0 : (Ke = null, qe = 0, n = Xe);
    }
    if (n !== 0) {
      if (n === 2 && (a = go(e), a !== 0 && (s = a, n = oa(e, a))), n === 1) throw o = Uo, rr(e, 0), An(e, s), at(e, Oe()), o;
      if (n === 6) An(e, s);
      else {
        if (a = e.current.alternate, (s & 30) === 0 && !o0(a) && (n = Ps(e, s), n === 2 && (d = go(e), d !== 0 && (s = d, n = oa(e, d))), n === 1)) throw o = Uo, rr(e, 0), An(e, s), at(e, Oe()), o;
        switch (e.finishedWork = a, e.finishedLanes = s, n) {
          case 0:
          case 1:
            throw Error(i(345));
          case 2:
            or(e, ut, an);
            break;
          case 3:
            if (An(e, s), (s & 130023424) === s && (n = ta + 500 - Oe(), 10 < n)) {
              if (xr(e, 0) !== 0) break;
              if (a = e.suspendedLanes, (a & s) !== s) {
                ot(), e.pingedLanes |= e.suspendedLanes & a;
                break;
              }
              e.timeoutHandle = cu(or.bind(null, e, ut, an), n);
              break;
            }
            or(e, ut, an);
            break;
          case 4:
            if (An(e, s), (s & 4194240) === s) break;
            for (n = e.eventTimes, a = -1; 0 < s; ) {
              var w = 31 - pt(s);
              d = 1 << w, w = n[w], w > a && (a = w), s &= ~d;
            }
            if (s = a, s = Oe() - s, s = (120 > s ? 120 : 480 > s ? 480 : 1080 > s ? 1080 : 1920 > s ? 1920 : 3e3 > s ? 3e3 : 4320 > s ? 4320 : 1960 * r0(s / 1960)) - s, 10 < s) {
              e.timeoutHandle = cu(or.bind(null, e, ut, an), s);
              break;
            }
            or(e, ut, an);
            break;
          case 5:
            or(e, ut, an);
            break;
          default:
            throw Error(i(329));
        }
      }
    }
    return at(e, Oe()), e.callbackNode === o ? Sd.bind(null, e) : null;
  }
  function oa(e, n) {
    var o = Yo;
    return e.current.memoizedState.isDehydrated && (rr(e, n).flags |= 256), e = Ps(e, n), e !== 2 && (n = ut, ut = o, n !== null && ia(n)), e;
  }
  function ia(e) {
    ut === null ? ut = e : ut.push.apply(ut, e);
  }
  function o0(e) {
    for (var n = e; ; ) {
      if (n.flags & 16384) {
        var o = n.updateQueue;
        if (o !== null && (o = o.stores, o !== null)) for (var s = 0; s < o.length; s++) {
          var a = o[s], d = a.getSnapshot;
          a = a.value;
          try {
            if (!zt(d(), a)) return !1;
          } catch {
            return !1;
          }
        }
      }
      if (o = n.child, n.subtreeFlags & 16384 && o !== null) o.return = n, n = o;
      else {
        if (n === e) break;
        for (; n.sibling === null; ) {
          if (n.return === null || n.return === e) return !0;
          n = n.return;
        }
        n.sibling.return = n.return, n = n.sibling;
      }
    }
    return !0;
  }
  function An(e, n) {
    for (n &= ~ea, n &= ~_s, e.suspendedLanes |= n, e.pingedLanes &= ~n, e = e.expirationTimes; 0 < n; ) {
      var o = 31 - pt(n), s = 1 << o;
      e[o] = -1, n &= ~s;
    }
  }
  function _d(e) {
    if ((Pe & 6) !== 0) throw Error(i(327));
    Wr();
    var n = xr(e, 0);
    if ((n & 1) === 0) return at(e, Oe()), null;
    var o = Ps(e, n);
    if (e.tag !== 0 && o === 2) {
      var s = go(e);
      s !== 0 && (n = s, o = oa(e, s));
    }
    if (o === 1) throw o = Uo, rr(e, 0), An(e, n), at(e, Oe()), o;
    if (o === 6) throw Error(i(345));
    return e.finishedWork = e.current.alternate, e.finishedLanes = n, or(e, ut, an), at(e, Oe()), null;
  }
  function sa(e, n) {
    var o = Pe;
    Pe |= 1;
    try {
      return e(n);
    } finally {
      Pe = o, Pe === 0 && (Br = Oe() + 500, ts && Cn());
    }
  }
  function nr(e) {
    Tn !== null && Tn.tag === 0 && (Pe & 6) === 0 && Wr();
    var n = Pe;
    Pe |= 1;
    var o = Mt.transition, s = ze;
    try {
      if (Mt.transition = null, ze = 1, e) return e();
    } finally {
      ze = s, Mt.transition = o, Pe = n, (Pe & 6) === 0 && Cn();
    }
  }
  function la() {
    vt = Vr.current, Re(Vr);
  }
  function rr(e, n) {
    e.finishedWork = null, e.finishedLanes = 0;
    var o = e.timeoutHandle;
    if (o !== -1 && (e.timeoutHandle = -1, Rm(o)), Ue !== null) for (o = Ue.return; o !== null; ) {
      var s = o;
      switch (mu(s), s.tag) {
        case 1:
          s = s.type.childContextTypes, s != null && Ji();
          break;
        case 3:
          Or(), Re(it), Re(Je), Iu();
          break;
        case 5:
          Mu(s);
          break;
        case 4:
          Or();
          break;
        case 13:
          Re(Fe);
          break;
        case 19:
          Re(Fe);
          break;
        case 10:
          _u(s.type._context);
          break;
        case 22:
        case 23:
          la();
      }
      o = o.return;
    }
    if (Ke = e, Ue = e = Rn(e.current, null), qe = vt = n, Xe = 0, Uo = null, ea = _s = tr = 0, ut = Yo = null, qn !== null) {
      for (n = 0; n < qn.length; n++) if (o = qn[n], s = o.interleaved, s !== null) {
        o.interleaved = null;
        var a = s.next, d = o.pending;
        if (d !== null) {
          var w = d.next;
          d.next = a, s.next = w;
        }
        o.pending = s;
      }
      qn = null;
    }
    return e;
  }
  function Ed(e, n) {
    do {
      var o = Ue;
      try {
        if (Su(), fs.current = gs, ds) {
          for (var s = He.memoizedState; s !== null; ) {
            var a = s.queue;
            a !== null && (a.pending = null), s = s.next;
          }
          ds = !1;
        }
        if (er = 0, Qe = be = He = null, Oo = !1, Fo = 0, Ju.current = null, o === null || o.return === null) {
          Xe = 1, Uo = n, Ue = null;
          break;
        }
        e: {
          var d = e, w = o.return, P = o, R = n;
          if (n = qe, P.flags |= 32768, R !== null && typeof R == "object" && typeof R.then == "function") {
            var Z = R, oe = P, ie = oe.tag;
            if ((oe.mode & 1) === 0 && (ie === 0 || ie === 11 || ie === 15)) {
              var ne = oe.alternate;
              ne ? (oe.updateQueue = ne.updateQueue, oe.memoizedState = ne.memoizedState, oe.lanes = ne.lanes) : (oe.updateQueue = null, oe.memoizedState = null);
            }
            var fe = Qf(w);
            if (fe !== null) {
              fe.flags &= -257, Kf(fe, w, P, d, n), fe.mode & 1 && Xf(d, Z, n), n = fe, R = Z;
              var he = n.updateQueue;
              if (he === null) {
                var pe = /* @__PURE__ */ new Set();
                pe.add(R), n.updateQueue = pe;
              } else he.add(R);
              break e;
            } else {
              if ((n & 1) === 0) {
                Xf(d, Z, n), ua();
                break e;
              }
              R = Error(i(426));
            }
          } else if (De && P.mode & 1) {
            var Be = Qf(w);
            if (Be !== null) {
              (Be.flags & 65536) === 0 && (Be.flags |= 256), Kf(Be, w, P, d, n), wu(Fr(R, P));
              break e;
            }
          }
          d = R = Fr(R, P), Xe !== 4 && (Xe = 2), Yo === null ? Yo = [d] : Yo.push(d), d = w;
          do {
            switch (d.tag) {
              case 3:
                d.flags |= 65536, n &= -n, d.lanes |= n;
                var Y = Yf(d, R, n);
                vf(d, Y);
                break e;
              case 1:
                P = R;
                var O = d.type, X = d.stateNode;
                if ((d.flags & 128) === 0 && (typeof O.getDerivedStateFromError == "function" || X !== null && typeof X.componentDidCatch == "function" && (zn === null || !zn.has(X)))) {
                  d.flags |= 65536, n &= -n, d.lanes |= n;
                  var se = bf(d, P, n);
                  vf(d, se);
                  break e;
                }
            }
            d = d.return;
          } while (d !== null);
        }
        Cd(o);
      } catch (ge) {
        n = ge, Ue === o && o !== null && (Ue = o = o.return);
        continue;
      }
      break;
    } while (!0);
  }
  function kd() {
    var e = Ss.current;
    return Ss.current = gs, e === null ? gs : e;
  }
  function ua() {
    (Xe === 0 || Xe === 3 || Xe === 2) && (Xe = 4), Ke === null || (tr & 268435455) === 0 && (_s & 268435455) === 0 || An(Ke, qe);
  }
  function Ps(e, n) {
    var o = Pe;
    Pe |= 2;
    var s = kd();
    (Ke !== e || qe !== n) && (an = null, rr(e, n));
    do
      try {
        i0();
        break;
      } catch (a) {
        Ed(e, a);
      }
    while (!0);
    if (Su(), Pe = o, Ss.current = s, Ue !== null) throw Error(i(261));
    return Ke = null, qe = 0, Xe;
  }
  function i0() {
    for (; Ue !== null; ) Nd(Ue);
  }
  function s0() {
    for (; Ue !== null && !Tl(); ) Nd(Ue);
  }
  function Nd(e) {
    var n = Id(e.alternate, e, vt);
    e.memoizedProps = e.pendingProps, n === null ? Cd(e) : Ue = n, Ju.current = null;
  }
  function Cd(e) {
    var n = e;
    do {
      var o = n.alternate;
      if (e = n.return, (n.flags & 32768) === 0) {
        if (o = qm(o, n, vt), o !== null) {
          Ue = o;
          return;
        }
      } else {
        if (o = Jm(o, n), o !== null) {
          o.flags &= 32767, Ue = o;
          return;
        }
        if (e !== null) e.flags |= 32768, e.subtreeFlags = 0, e.deletions = null;
        else {
          Xe = 6, Ue = null;
          return;
        }
      }
      if (n = n.sibling, n !== null) {
        Ue = n;
        return;
      }
      Ue = n = e;
    } while (n !== null);
    Xe === 0 && (Xe = 5);
  }
  function or(e, n, o) {
    var s = ze, a = Mt.transition;
    try {
      Mt.transition = null, ze = 1, l0(e, n, o, s);
    } finally {
      Mt.transition = a, ze = s;
    }
    return null;
  }
  function l0(e, n, o, s) {
    do
      Wr();
    while (Tn !== null);
    if ((Pe & 6) !== 0) throw Error(i(327));
    o = e.finishedWork;
    var a = e.finishedLanes;
    if (o === null) return null;
    if (e.finishedWork = null, e.finishedLanes = 0, o === e.current) throw Error(i(177));
    e.callbackNode = null, e.callbackPriority = 0;
    var d = o.lanes | o.childLanes;
    if (Di(e, d), e === Ke && (Ue = Ke = null, qe = 0), (o.subtreeFlags & 2064) === 0 && (o.flags & 2064) === 0 || ks || (ks = !0, zd(yr, function() {
      return Wr(), null;
    })), d = (o.flags & 15990) !== 0, (o.subtreeFlags & 15990) !== 0 || d) {
      d = Mt.transition, Mt.transition = null;
      var w = ze;
      ze = 1;
      var P = Pe;
      Pe |= 4, Ju.current = null, t0(e, o), md(o, e), Mm(uu), Fi = !!lu, uu = lu = null, e.current = o, n0(o), Li(), Pe = P, ze = w, Mt.transition = d;
    } else e.current = o;
    if (ks && (ks = !1, Tn = e, Ns = a), d = e.pendingLanes, d === 0 && (zn = null), Rl(o.stateNode), at(e, Oe()), n !== null) for (s = e.onRecoverableError, o = 0; o < n.length; o++) a = n[o], s(a.value, { componentStack: a.stack, digest: a.digest });
    if (Es) throw Es = !1, e = na, na = null, e;
    return (Ns & 1) !== 0 && e.tag !== 0 && Wr(), d = e.pendingLanes, (d & 1) !== 0 ? e === ra ? bo++ : (bo = 0, ra = e) : bo = 0, Cn(), null;
  }
  function Wr() {
    if (Tn !== null) {
      var e = hc(Ns), n = Mt.transition, o = ze;
      try {
        if (Mt.transition = null, ze = 16 > e ? 16 : e, Tn === null) var s = !1;
        else {
          if (e = Tn, Tn = null, Ns = 0, (Pe & 6) !== 0) throw Error(i(331));
          var a = Pe;
          for (Pe |= 4, de = e.current; de !== null; ) {
            var d = de, w = d.child;
            if ((de.flags & 16) !== 0) {
              var P = d.deletions;
              if (P !== null) {
                for (var R = 0; R < P.length; R++) {
                  var Z = P[R];
                  for (de = Z; de !== null; ) {
                    var oe = de;
                    switch (oe.tag) {
                      case 0:
                      case 11:
                      case 15:
                        Wo(8, oe, d);
                    }
                    var ie = oe.child;
                    if (ie !== null) ie.return = oe, de = ie;
                    else for (; de !== null; ) {
                      oe = de;
                      var ne = oe.sibling, fe = oe.return;
                      if (fd(oe), oe === Z) {
                        de = null;
                        break;
                      }
                      if (ne !== null) {
                        ne.return = fe, de = ne;
                        break;
                      }
                      de = fe;
                    }
                  }
                }
                var he = d.alternate;
                if (he !== null) {
                  var pe = he.child;
                  if (pe !== null) {
                    he.child = null;
                    do {
                      var Be = pe.sibling;
                      pe.sibling = null, pe = Be;
                    } while (pe !== null);
                  }
                }
                de = d;
              }
            }
            if ((d.subtreeFlags & 2064) !== 0 && w !== null) w.return = d, de = w;
            else e: for (; de !== null; ) {
              if (d = de, (d.flags & 2048) !== 0) switch (d.tag) {
                case 0:
                case 11:
                case 15:
                  Wo(9, d, d.return);
              }
              var Y = d.sibling;
              if (Y !== null) {
                Y.return = d.return, de = Y;
                break e;
              }
              de = d.return;
            }
          }
          var O = e.current;
          for (de = O; de !== null; ) {
            w = de;
            var X = w.child;
            if ((w.subtreeFlags & 2064) !== 0 && X !== null) X.return = w, de = X;
            else e: for (w = O; de !== null; ) {
              if (P = de, (P.flags & 2048) !== 0) try {
                switch (P.tag) {
                  case 0:
                  case 11:
                  case 15:
                    xs(9, P);
                }
              } catch (ge) {
                Ve(P, P.return, ge);
              }
              if (P === w) {
                de = null;
                break e;
              }
              var se = P.sibling;
              if (se !== null) {
                se.return = P.return, de = se;
                break e;
              }
              de = P.return;
            }
          }
          if (Pe = a, Cn(), _t && typeof _t.onPostCommitFiberRoot == "function") try {
            _t.onPostCommitFiberRoot(bn, e);
          } catch {
          }
          s = !0;
        }
        return s;
      } finally {
        ze = o, Mt.transition = n;
      }
    }
    return !1;
  }
  function Md(e, n, o) {
    n = Fr(o, n), n = Yf(e, n, 1), e = Pn(e, n, 1), n = ot(), e !== null && (Xn(e, 1, n), at(e, n));
  }
  function Ve(e, n, o) {
    if (e.tag === 3) Md(e, e, o);
    else for (; n !== null; ) {
      if (n.tag === 3) {
        Md(n, e, o);
        break;
      } else if (n.tag === 1) {
        var s = n.stateNode;
        if (typeof n.type.getDerivedStateFromError == "function" || typeof s.componentDidCatch == "function" && (zn === null || !zn.has(s))) {
          e = Fr(o, e), e = bf(n, e, 1), n = Pn(n, e, 1), e = ot(), n !== null && (Xn(n, 1, e), at(n, e));
          break;
        }
      }
      n = n.return;
    }
  }
  function u0(e, n, o) {
    var s = e.pingCache;
    s !== null && s.delete(n), n = ot(), e.pingedLanes |= e.suspendedLanes & o, Ke === e && (qe & o) === o && (Xe === 4 || Xe === 3 && (qe & 130023424) === qe && 500 > Oe() - ta ? rr(e, 0) : ea |= o), at(e, n);
  }
  function Pd(e, n) {
    n === 0 && ((e.mode & 1) === 0 ? n = 1 : (n = wr, wr <<= 1, (wr & 130023424) === 0 && (wr = 4194304)));
    var o = ot();
    e = sn(e, n), e !== null && (Xn(e, n, o), at(e, o));
  }
  function a0(e) {
    var n = e.memoizedState, o = 0;
    n !== null && (o = n.retryLane), Pd(e, o);
  }
  function c0(e, n) {
    var o = 0;
    switch (e.tag) {
      case 13:
        var s = e.stateNode, a = e.memoizedState;
        a !== null && (o = a.retryLane);
        break;
      case 19:
        s = e.stateNode;
        break;
      default:
        throw Error(i(314));
    }
    s !== null && s.delete(n), Pd(e, o);
  }
  var Id;
  Id = function(e, n, o) {
    if (e !== null) if (e.memoizedProps !== n.pendingProps || it.current) lt = !0;
    else {
      if ((e.lanes & o) === 0 && (n.flags & 128) === 0) return lt = !1, Zm(e, n, o);
      lt = (e.flags & 131072) !== 0;
    }
    else lt = !1, De && (n.flags & 1048576) !== 0 && uf(n, rs, n.index);
    switch (n.lanes = 0, n.tag) {
      case 2:
        var s = n.type;
        vs(e, n), e = n.pendingProps;
        var a = Tr(n, Je.current);
        jr(n, o), a = Lu(null, n, s, e, a, o);
        var d = Au();
        return n.flags |= 1, typeof a == "object" && a !== null && typeof a.render == "function" && a.$$typeof === void 0 ? (n.tag = 1, n.memoizedState = null, n.updateQueue = null, st(s) ? (d = !0, es(n)) : d = !1, n.memoizedState = a.state !== null && a.state !== void 0 ? a.state : null, Nu(n), a.updater = ms, n.stateNode = a, a._reactInternals = n, Fu(n, s, e, o), n = Wu(null, n, s, !0, d, o)) : (n.tag = 0, De && d && gu(n), rt(null, n, a, o), n = n.child), n;
      case 16:
        s = n.elementType;
        e: {
          switch (vs(e, n), e = n.pendingProps, a = s._init, s = a(s._payload), n.type = s, a = n.tag = d0(s), e = Lt(s, e), a) {
            case 0:
              n = Bu(null, n, s, e, o);
              break e;
            case 1:
              n = td(null, n, s, e, o);
              break e;
            case 11:
              n = Gf(null, n, s, e, o);
              break e;
            case 14:
              n = Zf(null, n, s, Lt(s.type, e), o);
              break e;
          }
          throw Error(i(
            306,
            s,
            ""
          ));
        }
        return n;
      case 0:
        return s = n.type, a = n.pendingProps, a = n.elementType === s ? a : Lt(s, a), Bu(e, n, s, a, o);
      case 1:
        return s = n.type, a = n.pendingProps, a = n.elementType === s ? a : Lt(s, a), td(e, n, s, a, o);
      case 3:
        e: {
          if (nd(n), e === null) throw Error(i(387));
          s = n.pendingProps, d = n.memoizedState, a = d.element, yf(e, n), as(n, s, null, o);
          var w = n.memoizedState;
          if (s = w.element, d.isDehydrated) if (d = { element: s, isDehydrated: !1, cache: w.cache, pendingSuspenseBoundaries: w.pendingSuspenseBoundaries, transitions: w.transitions }, n.updateQueue.baseState = d, n.memoizedState = d, n.flags & 256) {
            a = Fr(Error(i(423)), n), n = rd(e, n, s, o, a);
            break e;
          } else if (s !== a) {
            a = Fr(Error(i(424)), n), n = rd(e, n, s, o, a);
            break e;
          } else for (yt = En(n.stateNode.containerInfo.firstChild), mt = n, De = !0, Tt = null, o = gf(n, null, s, o), n.child = o; o; ) o.flags = o.flags & -3 | 4096, o = o.sibling;
          else {
            if (Rr(), s === a) {
              n = un(e, n, o);
              break e;
            }
            rt(e, n, s, o);
          }
          n = n.child;
        }
        return n;
      case 5:
        return xf(n), e === null && vu(n), s = n.type, a = n.pendingProps, d = e !== null ? e.memoizedProps : null, w = a.children, au(s, a) ? w = null : d !== null && au(s, d) && (n.flags |= 32), ed(e, n), rt(e, n, w, o), n.child;
      case 6:
        return e === null && vu(n), null;
      case 13:
        return od(e, n, o);
      case 4:
        return Cu(n, n.stateNode.containerInfo), s = n.pendingProps, e === null ? n.child = $r(n, null, s, o) : rt(e, n, s, o), n.child;
      case 11:
        return s = n.type, a = n.pendingProps, a = n.elementType === s ? a : Lt(s, a), Gf(e, n, s, a, o);
      case 7:
        return rt(e, n, n.pendingProps, o), n.child;
      case 8:
        return rt(e, n, n.pendingProps.children, o), n.child;
      case 12:
        return rt(e, n, n.pendingProps.children, o), n.child;
      case 10:
        e: {
          if (s = n.type._context, a = n.pendingProps, d = n.memoizedProps, w = a.value, Le(ss, s._currentValue), s._currentValue = w, d !== null) if (zt(d.value, w)) {
            if (d.children === a.children && !it.current) {
              n = un(e, n, o);
              break e;
            }
          } else for (d = n.child, d !== null && (d.return = n); d !== null; ) {
            var P = d.dependencies;
            if (P !== null) {
              w = d.child;
              for (var R = P.firstContext; R !== null; ) {
                if (R.context === s) {
                  if (d.tag === 1) {
                    R = ln(-1, o & -o), R.tag = 2;
                    var Z = d.updateQueue;
                    if (Z !== null) {
                      Z = Z.shared;
                      var oe = Z.pending;
                      oe === null ? R.next = R : (R.next = oe.next, oe.next = R), Z.pending = R;
                    }
                  }
                  d.lanes |= o, R = d.alternate, R !== null && (R.lanes |= o), Eu(
                    d.return,
                    o,
                    n
                  ), P.lanes |= o;
                  break;
                }
                R = R.next;
              }
            } else if (d.tag === 10) w = d.type === n.type ? null : d.child;
            else if (d.tag === 18) {
              if (w = d.return, w === null) throw Error(i(341));
              w.lanes |= o, P = w.alternate, P !== null && (P.lanes |= o), Eu(w, o, n), w = d.sibling;
            } else w = d.child;
            if (w !== null) w.return = d;
            else for (w = d; w !== null; ) {
              if (w === n) {
                w = null;
                break;
              }
              if (d = w.sibling, d !== null) {
                d.return = w.return, w = d;
                break;
              }
              w = w.return;
            }
            d = w;
          }
          rt(e, n, a.children, o), n = n.child;
        }
        return n;
      case 9:
        return a = n.type, s = n.pendingProps.children, jr(n, o), a = Nt(a), s = s(a), n.flags |= 1, rt(e, n, s, o), n.child;
      case 14:
        return s = n.type, a = Lt(s, n.pendingProps), a = Lt(s.type, a), Zf(e, n, s, a, o);
      case 15:
        return qf(e, n, n.type, n.pendingProps, o);
      case 17:
        return s = n.type, a = n.pendingProps, a = n.elementType === s ? a : Lt(s, a), vs(e, n), n.tag = 1, st(s) ? (e = !0, es(n)) : e = !1, jr(n, o), Wf(n, s, a), Fu(n, s, a, o), Wu(null, n, s, !0, e, o);
      case 19:
        return sd(e, n, o);
      case 22:
        return Jf(e, n, o);
    }
    throw Error(i(156, n.tag));
  };
  function zd(e, n) {
    return zi(e, n);
  }
  function f0(e, n, o, s) {
    this.tag = e, this.key = o, this.sibling = this.child = this.return = this.stateNode = this.type = this.elementType = null, this.index = 0, this.ref = null, this.pendingProps = n, this.dependencies = this.memoizedState = this.updateQueue = this.memoizedProps = null, this.mode = s, this.subtreeFlags = this.flags = 0, this.deletions = null, this.childLanes = this.lanes = 0, this.alternate = null;
  }
  function Pt(e, n, o, s) {
    return new f0(e, n, o, s);
  }
  function aa(e) {
    return e = e.prototype, !(!e || !e.isReactComponent);
  }
  function d0(e) {
    if (typeof e == "function") return aa(e) ? 1 : 0;
    if (e != null) {
      if (e = e.$$typeof, e === G) return 11;
      if (e === B) return 14;
    }
    return 2;
  }
  function Rn(e, n) {
    var o = e.alternate;
    return o === null ? (o = Pt(e.tag, n, e.key, e.mode), o.elementType = e.elementType, o.type = e.type, o.stateNode = e.stateNode, o.alternate = e, e.alternate = o) : (o.pendingProps = n, o.type = e.type, o.flags = 0, o.subtreeFlags = 0, o.deletions = null), o.flags = e.flags & 14680064, o.childLanes = e.childLanes, o.lanes = e.lanes, o.child = e.child, o.memoizedProps = e.memoizedProps, o.memoizedState = e.memoizedState, o.updateQueue = e.updateQueue, n = e.dependencies, o.dependencies = n === null ? null : { lanes: n.lanes, firstContext: n.firstContext }, o.sibling = e.sibling, o.index = e.index, o.ref = e.ref, o;
  }
  function Is(e, n, o, s, a, d) {
    var w = 2;
    if (s = e, typeof e == "function") aa(e) && (w = 1);
    else if (typeof e == "string") w = 5;
    else e: switch (e) {
      case H:
        return ir(o.children, a, d, n);
      case b:
        w = 8, a |= 8;
        break;
      case K:
        return e = Pt(12, o, n, a | 2), e.elementType = K, e.lanes = d, e;
      case Q:
        return e = Pt(13, o, n, a), e.elementType = Q, e.lanes = d, e;
      case z:
        return e = Pt(19, o, n, a), e.elementType = z, e.lanes = d, e;
      case W:
        return zs(o, a, d, n);
      default:
        if (typeof e == "object" && e !== null) switch (e.$$typeof) {
          case ee:
            w = 10;
            break e;
          case U:
            w = 9;
            break e;
          case G:
            w = 11;
            break e;
          case B:
            w = 14;
            break e;
          case F:
            w = 16, s = null;
            break e;
        }
        throw Error(i(130, e == null ? e : typeof e, ""));
    }
    return n = Pt(w, o, n, a), n.elementType = e, n.type = s, n.lanes = d, n;
  }
  function ir(e, n, o, s) {
    return e = Pt(7, e, s, n), e.lanes = o, e;
  }
  function zs(e, n, o, s) {
    return e = Pt(22, e, s, n), e.elementType = W, e.lanes = o, e.stateNode = { isHidden: !1 }, e;
  }
  function ca(e, n, o) {
    return e = Pt(6, e, null, n), e.lanes = o, e;
  }
  function fa(e, n, o) {
    return n = Pt(4, e.children !== null ? e.children : [], e.key, n), n.lanes = o, n.stateNode = { containerInfo: e.containerInfo, pendingChildren: null, implementation: e.implementation }, n;
  }
  function h0(e, n, o, s, a) {
    this.tag = n, this.containerInfo = e, this.finishedWork = this.pingCache = this.current = this.pendingChildren = null, this.timeoutHandle = -1, this.callbackNode = this.pendingContext = this.context = null, this.callbackPriority = 0, this.eventTimes = mo(0), this.expirationTimes = mo(-1), this.entangledLanes = this.finishedLanes = this.mutableReadLanes = this.expiredLanes = this.pingedLanes = this.suspendedLanes = this.pendingLanes = 0, this.entanglements = mo(0), this.identifierPrefix = s, this.onRecoverableError = a, this.mutableSourceEagerHydrationData = null;
  }
  function da(e, n, o, s, a, d, w, P, R) {
    return e = new h0(e, n, o, P, R), n === 1 ? (n = 1, d === !0 && (n |= 8)) : n = 0, d = Pt(3, null, null, n), e.current = d, d.stateNode = e, d.memoizedState = { element: s, isDehydrated: o, cache: null, transitions: null, pendingSuspenseBoundaries: null }, Nu(d), e;
  }
  function p0(e, n, o) {
    var s = 3 < arguments.length && arguments[3] !== void 0 ? arguments[3] : null;
    return { $$typeof: j, key: s == null ? null : "" + s, children: e, containerInfo: n, implementation: o };
  }
  function Td(e) {
    if (!e) return Nn;
    e = e._reactInternals;
    e: {
      if (Vt(e) !== e || e.tag !== 1) throw Error(i(170));
      var n = e;
      do {
        switch (n.tag) {
          case 3:
            n = n.stateNode.context;
            break e;
          case 1:
            if (st(n.type)) {
              n = n.stateNode.__reactInternalMemoizedMergedChildContext;
              break e;
            }
        }
        n = n.return;
      } while (n !== null);
      throw Error(i(171));
    }
    if (e.tag === 1) {
      var o = e.type;
      if (st(o)) return of(e, o, n);
    }
    return n;
  }
  function Ld(e, n, o, s, a, d, w, P, R) {
    return e = da(o, s, !0, e, a, d, w, P, R), e.context = Td(null), o = e.current, s = ot(), a = Ln(o), d = ln(s, a), d.callback = n ?? null, Pn(o, d, a), e.current.lanes = a, Xn(e, a, s), at(e, s), e;
  }
  function Ts(e, n, o, s) {
    var a = n.current, d = ot(), w = Ln(a);
    return o = Td(o), n.context === null ? n.context = o : n.pendingContext = o, n = ln(d, w), n.payload = { element: e }, s = s === void 0 ? null : s, s !== null && (n.callback = s), e = Pn(a, n, w), e !== null && ($t(e, a, w, d), us(e, a, w)), w;
  }
  function Ls(e) {
    if (e = e.current, !e.child) return null;
    switch (e.child.tag) {
      case 5:
        return e.child.stateNode;
      default:
        return e.child.stateNode;
    }
  }
  function Ad(e, n) {
    if (e = e.memoizedState, e !== null && e.dehydrated !== null) {
      var o = e.retryLane;
      e.retryLane = o !== 0 && o < n ? o : n;
    }
  }
  function ha(e, n) {
    Ad(e, n), (e = e.alternate) && Ad(e, n);
  }
  function g0() {
    return null;
  }
  var Rd = typeof reportError == "function" ? reportError : function(e) {
    console.error(e);
  };
  function pa(e) {
    this._internalRoot = e;
  }
  As.prototype.render = pa.prototype.render = function(e) {
    var n = this._internalRoot;
    if (n === null) throw Error(i(409));
    Ts(e, n, null, null);
  }, As.prototype.unmount = pa.prototype.unmount = function() {
    var e = this._internalRoot;
    if (e !== null) {
      this._internalRoot = null;
      var n = e.containerInfo;
      nr(function() {
        Ts(null, e, null, null);
      }), n[tn] = null;
    }
  };
  function As(e) {
    this._internalRoot = e;
  }
  As.prototype.unstable_scheduleHydration = function(e) {
    if (e) {
      var n = mc();
      e = { blockedOn: null, target: e, priority: n };
      for (var o = 0; o < xn.length && n !== 0 && n < xn[o].priority; o++) ;
      xn.splice(o, 0, e), o === 0 && wc(e);
    }
  };
  function ga(e) {
    return !(!e || e.nodeType !== 1 && e.nodeType !== 9 && e.nodeType !== 11);
  }
  function Rs(e) {
    return !(!e || e.nodeType !== 1 && e.nodeType !== 9 && e.nodeType !== 11 && (e.nodeType !== 8 || e.nodeValue !== " react-mount-point-unstable "));
  }
  function $d() {
  }
  function m0(e, n, o, s, a) {
    if (a) {
      if (typeof s == "function") {
        var d = s;
        s = function() {
          var Z = Ls(w);
          d.call(Z);
        };
      }
      var w = Ld(n, s, e, 0, null, !1, !1, "", $d);
      return e._reactRootContainer = w, e[tn] = w.current, zo(e.nodeType === 8 ? e.parentNode : e), nr(), w;
    }
    for (; a = e.lastChild; ) e.removeChild(a);
    if (typeof s == "function") {
      var P = s;
      s = function() {
        var Z = Ls(R);
        P.call(Z);
      };
    }
    var R = da(e, 0, !1, null, null, !1, !1, "", $d);
    return e._reactRootContainer = R, e[tn] = R.current, zo(e.nodeType === 8 ? e.parentNode : e), nr(function() {
      Ts(n, R, o, s);
    }), R;
  }
  function $s(e, n, o, s, a) {
    var d = o._reactRootContainer;
    if (d) {
      var w = d;
      if (typeof a == "function") {
        var P = a;
        a = function() {
          var R = Ls(w);
          P.call(R);
        };
      }
      Ts(n, w, e, a);
    } else w = m0(o, n, e, a, s);
    return Ls(w);
  }
  pc = function(e) {
    switch (e.tag) {
      case 3:
        var n = e.stateNode;
        if (n.current.memoizedState.isDehydrated) {
          var o = Bt(n.pendingLanes);
          o !== 0 && (Fl(n, o | 1), at(n, Oe()), (Pe & 6) === 0 && (Br = Oe() + 500, Cn()));
        }
        break;
      case 13:
        nr(function() {
          var s = sn(e, 1);
          if (s !== null) {
            var a = ot();
            $t(s, e, 1, a);
          }
        }), ha(e, 1);
    }
  }, Hl = function(e) {
    if (e.tag === 13) {
      var n = sn(e, 134217728);
      if (n !== null) {
        var o = ot();
        $t(n, e, 134217728, o);
      }
      ha(e, 134217728);
    }
  }, gc = function(e) {
    if (e.tag === 13) {
      var n = Ln(e), o = sn(e, n);
      if (o !== null) {
        var s = ot();
        $t(o, e, n, s);
      }
      ha(e, n);
    }
  }, mc = function() {
    return ze;
  }, yc = function(e, n) {
    var o = ze;
    try {
      return ze = e, n();
    } finally {
      ze = o;
    }
  }, lo = function(e, n, o) {
    switch (n) {
      case "input":
        if (We(e, o), n = o.name, o.type === "radio" && n != null) {
          for (o = e; o.parentNode; ) o = o.parentNode;
          for (o = o.querySelectorAll("input[name=" + JSON.stringify("" + n) + '][type="radio"]'), n = 0; n < o.length; n++) {
            var s = o[n];
            if (s !== e && s.form === e.form) {
              var a = qi(s);
              if (!a) throw Error(i(90));
              Se(s), We(s, a);
            }
          }
        }
        break;
      case "textarea":
        pr(e, o);
        break;
      case "select":
        n = o.value, n != null && St(e, !!o.multiple, n, !1);
    }
  }, Ni = sa, Ci = nr;
  var y0 = { usingClientEntryPoint: !1, Events: [Ao, Ir, qi, Ei, ki, sa] }, Xo = { findFiberByHostInstance: Qn, bundleType: 0, version: "18.3.1", rendererPackageName: "react-dom" }, v0 = { bundleType: Xo.bundleType, version: Xo.version, rendererPackageName: Xo.rendererPackageName, rendererConfig: Xo.rendererConfig, overrideHookState: null, overrideHookStateDeletePath: null, overrideHookStateRenamePath: null, overrideProps: null, overridePropsDeletePath: null, overridePropsRenamePath: null, setErrorHandler: null, setSuspenseHandler: null, scheduleUpdate: null, currentDispatcherRef: E.ReactCurrentDispatcher, findHostInstanceByFiber: function(e) {
    return e = Pi(e), e === null ? null : e.stateNode;
  }, findFiberByHostInstance: Xo.findFiberByHostInstance || g0, findHostInstancesForRefresh: null, scheduleRefresh: null, scheduleRoot: null, setRefreshHandler: null, getCurrentFiber: null, reconcilerVersion: "18.3.1-next-f1338f8080-20240426" };
  if (typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ < "u") {
    var Ds = __REACT_DEVTOOLS_GLOBAL_HOOK__;
    if (!Ds.isDisabled && Ds.supportsFiber) try {
      bn = Ds.inject(v0), _t = Ds;
    } catch {
    }
  }
  return ct.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED = y0, ct.createPortal = function(e, n) {
    var o = 2 < arguments.length && arguments[2] !== void 0 ? arguments[2] : null;
    if (!ga(n)) throw Error(i(200));
    return p0(e, n, null, o);
  }, ct.createRoot = function(e, n) {
    if (!ga(e)) throw Error(i(299));
    var o = !1, s = "", a = Rd;
    return n != null && (n.unstable_strictMode === !0 && (o = !0), n.identifierPrefix !== void 0 && (s = n.identifierPrefix), n.onRecoverableError !== void 0 && (a = n.onRecoverableError)), n = da(e, 1, !1, null, null, o, !1, s, a), e[tn] = n.current, zo(e.nodeType === 8 ? e.parentNode : e), new pa(n);
  }, ct.findDOMNode = function(e) {
    if (e == null) return null;
    if (e.nodeType === 1) return e;
    var n = e._reactInternals;
    if (n === void 0)
      throw typeof e.render == "function" ? Error(i(188)) : (e = Object.keys(e).join(","), Error(i(268, e)));
    return e = Pi(n), e = e === null ? null : e.stateNode, e;
  }, ct.flushSync = function(e) {
    return nr(e);
  }, ct.hydrate = function(e, n, o) {
    if (!Rs(n)) throw Error(i(200));
    return $s(null, e, n, !0, o);
  }, ct.hydrateRoot = function(e, n, o) {
    if (!ga(e)) throw Error(i(405));
    var s = o != null && o.hydratedSources || null, a = !1, d = "", w = Rd;
    if (o != null && (o.unstable_strictMode === !0 && (a = !0), o.identifierPrefix !== void 0 && (d = o.identifierPrefix), o.onRecoverableError !== void 0 && (w = o.onRecoverableError)), n = Ld(n, null, e, 1, o ?? null, a, !1, d, w), e[tn] = n.current, zo(e), s) for (e = 0; e < s.length; e++) o = s[e], a = o._getVersion, a = a(o._source), n.mutableSourceEagerHydrationData == null ? n.mutableSourceEagerHydrationData = [o, a] : n.mutableSourceEagerHydrationData.push(
      o,
      a
    );
    return new As(n);
  }, ct.render = function(e, n, o) {
    if (!Rs(n)) throw Error(i(200));
    return $s(null, e, n, !1, o);
  }, ct.unmountComponentAtNode = function(e) {
    if (!Rs(e)) throw Error(i(40));
    return e._reactRootContainer ? (nr(function() {
      $s(null, null, e, !1, function() {
        e._reactRootContainer = null, e[tn] = null;
      });
    }), !0) : !1;
  }, ct.unstable_batchedUpdates = sa, ct.unstable_renderSubtreeIntoContainer = function(e, n, o, s) {
    if (!Rs(o)) throw Error(i(200));
    if (e == null || e._reactInternals === void 0) throw Error(i(38));
    return $s(e, n, o, !1, s);
  }, ct.version = "18.3.1-next-f1338f8080-20240426", ct;
}
var Wd;
function ep() {
  if (Wd) return va.exports;
  Wd = 1;
  function t() {
    if (!(typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ > "u" || typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE != "function"))
      try {
        __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE(t);
      } catch (r) {
        console.error(r);
      }
  }
  return t(), va.exports = C0(), va.exports;
}
var Ud;
function M0() {
  if (Ud) return js;
  Ud = 1;
  var t = ep();
  return js.createRoot = t.createRoot, js.hydrateRoot = t.hydrateRoot, js;
}
var P0 = M0();
function Ye(t) {
  if (typeof t == "string" || typeof t == "number") return "" + t;
  let r = "";
  if (Array.isArray(t))
    for (let i = 0, l; i < t.length; i++)
      (l = Ye(t[i])) !== "" && (r += (r && " ") + l);
  else
    for (let i in t)
      t[i] && (r += (r && " ") + i);
  return r;
}
var I0 = { value: () => {
} };
function dl() {
  for (var t = 0, r = arguments.length, i = {}, l; t < r; ++t) {
    if (!(l = arguments[t] + "") || l in i || /[\s.]/.test(l)) throw new Error("illegal type: " + l);
    i[l] = [];
  }
  return new Qs(i);
}
function Qs(t) {
  this._ = t;
}
function z0(t, r) {
  return t.trim().split(/^|\s+/).map(function(i) {
    var l = "", u = i.indexOf(".");
    if (u >= 0 && (l = i.slice(u + 1), i = i.slice(0, u)), i && !r.hasOwnProperty(i)) throw new Error("unknown type: " + i);
    return { type: i, name: l };
  });
}
Qs.prototype = dl.prototype = {
  constructor: Qs,
  on: function(t, r) {
    var i = this._, l = z0(t + "", i), u, c = -1, f = l.length;
    if (arguments.length < 2) {
      for (; ++c < f; ) if ((u = (t = l[c]).type) && (u = T0(i[u], t.name))) return u;
      return;
    }
    if (r != null && typeof r != "function") throw new Error("invalid callback: " + r);
    for (; ++c < f; )
      if (u = (t = l[c]).type) i[u] = Yd(i[u], t.name, r);
      else if (r == null) for (u in i) i[u] = Yd(i[u], t.name, null);
    return this;
  },
  copy: function() {
    var t = {}, r = this._;
    for (var i in r) t[i] = r[i].slice();
    return new Qs(t);
  },
  call: function(t, r) {
    if ((u = arguments.length - 2) > 0) for (var i = new Array(u), l = 0, u, c; l < u; ++l) i[l] = arguments[l + 2];
    if (!this._.hasOwnProperty(t)) throw new Error("unknown type: " + t);
    for (c = this._[t], l = 0, u = c.length; l < u; ++l) c[l].value.apply(r, i);
  },
  apply: function(t, r, i) {
    if (!this._.hasOwnProperty(t)) throw new Error("unknown type: " + t);
    for (var l = this._[t], u = 0, c = l.length; u < c; ++u) l[u].value.apply(r, i);
  }
};
function T0(t, r) {
  for (var i = 0, l = t.length, u; i < l; ++i)
    if ((u = t[i]).name === r)
      return u.value;
}
function Yd(t, r, i) {
  for (var l = 0, u = t.length; l < u; ++l)
    if (t[l].name === r) {
      t[l] = I0, t = t.slice(0, l).concat(t.slice(l + 1));
      break;
    }
  return i != null && t.push({ name: r, value: i }), t;
}
var Ra = "http://www.w3.org/1999/xhtml";
const bd = {
  svg: "http://www.w3.org/2000/svg",
  xhtml: Ra,
  xlink: "http://www.w3.org/1999/xlink",
  xml: "http://www.w3.org/XML/1998/namespace",
  xmlns: "http://www.w3.org/2000/xmlns/"
};
function hl(t) {
  var r = t += "", i = r.indexOf(":");
  return i >= 0 && (r = t.slice(0, i)) !== "xmlns" && (t = t.slice(i + 1)), bd.hasOwnProperty(r) ? { space: bd[r], local: t } : t;
}
function L0(t) {
  return function() {
    var r = this.ownerDocument, i = this.namespaceURI;
    return i === Ra && r.documentElement.namespaceURI === Ra ? r.createElement(t) : r.createElementNS(i, t);
  };
}
function A0(t) {
  return function() {
    return this.ownerDocument.createElementNS(t.space, t.local);
  };
}
function tp(t) {
  var r = hl(t);
  return (r.local ? A0 : L0)(r);
}
function R0() {
}
function Ka(t) {
  return t == null ? R0 : function() {
    return this.querySelector(t);
  };
}
function $0(t) {
  typeof t != "function" && (t = Ka(t));
  for (var r = this._groups, i = r.length, l = new Array(i), u = 0; u < i; ++u)
    for (var c = r[u], f = c.length, h = l[u] = new Array(f), p, m, v = 0; v < f; ++v)
      (p = c[v]) && (m = t.call(p, p.__data__, v, c)) && ("__data__" in p && (m.__data__ = p.__data__), h[v] = m);
  return new xt(l, this._parents);
}
function D0(t) {
  return t == null ? [] : Array.isArray(t) ? t : Array.from(t);
}
function j0() {
  return [];
}
function np(t) {
  return t == null ? j0 : function() {
    return this.querySelectorAll(t);
  };
}
function O0(t) {
  return function() {
    return D0(t.apply(this, arguments));
  };
}
function F0(t) {
  typeof t == "function" ? t = O0(t) : t = np(t);
  for (var r = this._groups, i = r.length, l = [], u = [], c = 0; c < i; ++c)
    for (var f = r[c], h = f.length, p, m = 0; m < h; ++m)
      (p = f[m]) && (l.push(t.call(p, p.__data__, m, f)), u.push(p));
  return new xt(l, u);
}
function rp(t) {
  return function() {
    return this.matches(t);
  };
}
function op(t) {
  return function(r) {
    return r.matches(t);
  };
}
var H0 = Array.prototype.find;
function V0(t) {
  return function() {
    return H0.call(this.children, t);
  };
}
function B0() {
  return this.firstElementChild;
}
function W0(t) {
  return this.select(t == null ? B0 : V0(typeof t == "function" ? t : op(t)));
}
var U0 = Array.prototype.filter;
function Y0() {
  return Array.from(this.children);
}
function b0(t) {
  return function() {
    return U0.call(this.children, t);
  };
}
function X0(t) {
  return this.selectAll(t == null ? Y0 : b0(typeof t == "function" ? t : op(t)));
}
function Q0(t) {
  typeof t != "function" && (t = rp(t));
  for (var r = this._groups, i = r.length, l = new Array(i), u = 0; u < i; ++u)
    for (var c = r[u], f = c.length, h = l[u] = [], p, m = 0; m < f; ++m)
      (p = c[m]) && t.call(p, p.__data__, m, c) && h.push(p);
  return new xt(l, this._parents);
}
function ip(t) {
  return new Array(t.length);
}
function K0() {
  return new xt(this._enter || this._groups.map(ip), this._parents);
}
function Js(t, r) {
  this.ownerDocument = t.ownerDocument, this.namespaceURI = t.namespaceURI, this._next = null, this._parent = t, this.__data__ = r;
}
Js.prototype = {
  constructor: Js,
  appendChild: function(t) {
    return this._parent.insertBefore(t, this._next);
  },
  insertBefore: function(t, r) {
    return this._parent.insertBefore(t, r);
  },
  querySelector: function(t) {
    return this._parent.querySelector(t);
  },
  querySelectorAll: function(t) {
    return this._parent.querySelectorAll(t);
  }
};
function G0(t) {
  return function() {
    return t;
  };
}
function Z0(t, r, i, l, u, c) {
  for (var f = 0, h, p = r.length, m = c.length; f < m; ++f)
    (h = r[f]) ? (h.__data__ = c[f], l[f] = h) : i[f] = new Js(t, c[f]);
  for (; f < p; ++f)
    (h = r[f]) && (u[f] = h);
}
function q0(t, r, i, l, u, c, f) {
  var h, p, m = /* @__PURE__ */ new Map(), v = r.length, g = c.length, y = new Array(v), x;
  for (h = 0; h < v; ++h)
    (p = r[h]) && (y[h] = x = f.call(p, p.__data__, h, r) + "", m.has(x) ? u[h] = p : m.set(x, p));
  for (h = 0; h < g; ++h)
    x = f.call(t, c[h], h, c) + "", (p = m.get(x)) ? (l[h] = p, p.__data__ = c[h], m.delete(x)) : i[h] = new Js(t, c[h]);
  for (h = 0; h < v; ++h)
    (p = r[h]) && m.get(y[h]) === p && (u[h] = p);
}
function J0(t) {
  return t.__data__;
}
function ey(t, r) {
  if (!arguments.length) return Array.from(this, J0);
  var i = r ? q0 : Z0, l = this._parents, u = this._groups;
  typeof t != "function" && (t = G0(t));
  for (var c = u.length, f = new Array(c), h = new Array(c), p = new Array(c), m = 0; m < c; ++m) {
    var v = l[m], g = u[m], y = g.length, x = ty(t.call(v, v && v.__data__, m, l)), S = x.length, M = h[m] = new Array(S), N = f[m] = new Array(S), k = p[m] = new Array(y);
    i(v, g, M, N, k, x, r);
    for (var $ = 0, _ = 0, E, D; $ < S; ++$)
      if (E = M[$]) {
        for ($ >= _ && (_ = $ + 1); !(D = N[_]) && ++_ < S; ) ;
        E._next = D || null;
      }
  }
  return f = new xt(f, l), f._enter = h, f._exit = p, f;
}
function ty(t) {
  return typeof t == "object" && "length" in t ? t : Array.from(t);
}
function ny() {
  return new xt(this._exit || this._groups.map(ip), this._parents);
}
function ry(t, r, i) {
  var l = this.enter(), u = this, c = this.exit();
  return typeof t == "function" ? (l = t(l), l && (l = l.selection())) : l = l.append(t + ""), r != null && (u = r(u), u && (u = u.selection())), i == null ? c.remove() : i(c), l && u ? l.merge(u).order() : u;
}
function oy(t) {
  for (var r = t.selection ? t.selection() : t, i = this._groups, l = r._groups, u = i.length, c = l.length, f = Math.min(u, c), h = new Array(u), p = 0; p < f; ++p)
    for (var m = i[p], v = l[p], g = m.length, y = h[p] = new Array(g), x, S = 0; S < g; ++S)
      (x = m[S] || v[S]) && (y[S] = x);
  for (; p < u; ++p)
    h[p] = i[p];
  return new xt(h, this._parents);
}
function iy() {
  for (var t = this._groups, r = -1, i = t.length; ++r < i; )
    for (var l = t[r], u = l.length - 1, c = l[u], f; --u >= 0; )
      (f = l[u]) && (c && f.compareDocumentPosition(c) ^ 4 && c.parentNode.insertBefore(f, c), c = f);
  return this;
}
function sy(t) {
  t || (t = ly);
  function r(g, y) {
    return g && y ? t(g.__data__, y.__data__) : !g - !y;
  }
  for (var i = this._groups, l = i.length, u = new Array(l), c = 0; c < l; ++c) {
    for (var f = i[c], h = f.length, p = u[c] = new Array(h), m, v = 0; v < h; ++v)
      (m = f[v]) && (p[v] = m);
    p.sort(r);
  }
  return new xt(u, this._parents).order();
}
function ly(t, r) {
  return t < r ? -1 : t > r ? 1 : t >= r ? 0 : NaN;
}
function uy() {
  var t = arguments[0];
  return arguments[0] = this, t.apply(null, arguments), this;
}
function ay() {
  return Array.from(this);
}
function cy() {
  for (var t = this._groups, r = 0, i = t.length; r < i; ++r)
    for (var l = t[r], u = 0, c = l.length; u < c; ++u) {
      var f = l[u];
      if (f) return f;
    }
  return null;
}
function fy() {
  let t = 0;
  for (const r of this) ++t;
  return t;
}
function dy() {
  return !this.node();
}
function hy(t) {
  for (var r = this._groups, i = 0, l = r.length; i < l; ++i)
    for (var u = r[i], c = 0, f = u.length, h; c < f; ++c)
      (h = u[c]) && t.call(h, h.__data__, c, u);
  return this;
}
function py(t) {
  return function() {
    this.removeAttribute(t);
  };
}
function gy(t) {
  return function() {
    this.removeAttributeNS(t.space, t.local);
  };
}
function my(t, r) {
  return function() {
    this.setAttribute(t, r);
  };
}
function yy(t, r) {
  return function() {
    this.setAttributeNS(t.space, t.local, r);
  };
}
function vy(t, r) {
  return function() {
    var i = r.apply(this, arguments);
    i == null ? this.removeAttribute(t) : this.setAttribute(t, i);
  };
}
function wy(t, r) {
  return function() {
    var i = r.apply(this, arguments);
    i == null ? this.removeAttributeNS(t.space, t.local) : this.setAttributeNS(t.space, t.local, i);
  };
}
function xy(t, r) {
  var i = hl(t);
  if (arguments.length < 2) {
    var l = this.node();
    return i.local ? l.getAttributeNS(i.space, i.local) : l.getAttribute(i);
  }
  return this.each((r == null ? i.local ? gy : py : typeof r == "function" ? i.local ? wy : vy : i.local ? yy : my)(i, r));
}
function sp(t) {
  return t.ownerDocument && t.ownerDocument.defaultView || t.document && t || t.defaultView;
}
function Sy(t) {
  return function() {
    this.style.removeProperty(t);
  };
}
function _y(t, r, i) {
  return function() {
    this.style.setProperty(t, r, i);
  };
}
function Ey(t, r, i) {
  return function() {
    var l = r.apply(this, arguments);
    l == null ? this.style.removeProperty(t) : this.style.setProperty(t, l, i);
  };
}
function ky(t, r, i) {
  return arguments.length > 1 ? this.each((r == null ? Sy : typeof r == "function" ? Ey : _y)(t, r, i ?? "")) : Kr(this.node(), t);
}
function Kr(t, r) {
  return t.style.getPropertyValue(r) || sp(t).getComputedStyle(t, null).getPropertyValue(r);
}
function Ny(t) {
  return function() {
    delete this[t];
  };
}
function Cy(t, r) {
  return function() {
    this[t] = r;
  };
}
function My(t, r) {
  return function() {
    var i = r.apply(this, arguments);
    i == null ? delete this[t] : this[t] = i;
  };
}
function Py(t, r) {
  return arguments.length > 1 ? this.each((r == null ? Ny : typeof r == "function" ? My : Cy)(t, r)) : this.node()[t];
}
function lp(t) {
  return t.trim().split(/^|\s+/);
}
function Ga(t) {
  return t.classList || new up(t);
}
function up(t) {
  this._node = t, this._names = lp(t.getAttribute("class") || "");
}
up.prototype = {
  add: function(t) {
    var r = this._names.indexOf(t);
    r < 0 && (this._names.push(t), this._node.setAttribute("class", this._names.join(" ")));
  },
  remove: function(t) {
    var r = this._names.indexOf(t);
    r >= 0 && (this._names.splice(r, 1), this._node.setAttribute("class", this._names.join(" ")));
  },
  contains: function(t) {
    return this._names.indexOf(t) >= 0;
  }
};
function ap(t, r) {
  for (var i = Ga(t), l = -1, u = r.length; ++l < u; ) i.add(r[l]);
}
function cp(t, r) {
  for (var i = Ga(t), l = -1, u = r.length; ++l < u; ) i.remove(r[l]);
}
function Iy(t) {
  return function() {
    ap(this, t);
  };
}
function zy(t) {
  return function() {
    cp(this, t);
  };
}
function Ty(t, r) {
  return function() {
    (r.apply(this, arguments) ? ap : cp)(this, t);
  };
}
function Ly(t, r) {
  var i = lp(t + "");
  if (arguments.length < 2) {
    for (var l = Ga(this.node()), u = -1, c = i.length; ++u < c; ) if (!l.contains(i[u])) return !1;
    return !0;
  }
  return this.each((typeof r == "function" ? Ty : r ? Iy : zy)(i, r));
}
function Ay() {
  this.textContent = "";
}
function Ry(t) {
  return function() {
    this.textContent = t;
  };
}
function $y(t) {
  return function() {
    var r = t.apply(this, arguments);
    this.textContent = r ?? "";
  };
}
function Dy(t) {
  return arguments.length ? this.each(t == null ? Ay : (typeof t == "function" ? $y : Ry)(t)) : this.node().textContent;
}
function jy() {
  this.innerHTML = "";
}
function Oy(t) {
  return function() {
    this.innerHTML = t;
  };
}
function Fy(t) {
  return function() {
    var r = t.apply(this, arguments);
    this.innerHTML = r ?? "";
  };
}
function Hy(t) {
  return arguments.length ? this.each(t == null ? jy : (typeof t == "function" ? Fy : Oy)(t)) : this.node().innerHTML;
}
function Vy() {
  this.nextSibling && this.parentNode.appendChild(this);
}
function By() {
  return this.each(Vy);
}
function Wy() {
  this.previousSibling && this.parentNode.insertBefore(this, this.parentNode.firstChild);
}
function Uy() {
  return this.each(Wy);
}
function Yy(t) {
  var r = typeof t == "function" ? t : tp(t);
  return this.select(function() {
    return this.appendChild(r.apply(this, arguments));
  });
}
function by() {
  return null;
}
function Xy(t, r) {
  var i = typeof t == "function" ? t : tp(t), l = r == null ? by : typeof r == "function" ? r : Ka(r);
  return this.select(function() {
    return this.insertBefore(i.apply(this, arguments), l.apply(this, arguments) || null);
  });
}
function Qy() {
  var t = this.parentNode;
  t && t.removeChild(this);
}
function Ky() {
  return this.each(Qy);
}
function Gy() {
  var t = this.cloneNode(!1), r = this.parentNode;
  return r ? r.insertBefore(t, this.nextSibling) : t;
}
function Zy() {
  var t = this.cloneNode(!0), r = this.parentNode;
  return r ? r.insertBefore(t, this.nextSibling) : t;
}
function qy(t) {
  return this.select(t ? Zy : Gy);
}
function Jy(t) {
  return arguments.length ? this.property("__data__", t) : this.node().__data__;
}
function ev(t) {
  return function(r) {
    t.call(this, r, this.__data__);
  };
}
function tv(t) {
  return t.trim().split(/^|\s+/).map(function(r) {
    var i = "", l = r.indexOf(".");
    return l >= 0 && (i = r.slice(l + 1), r = r.slice(0, l)), { type: r, name: i };
  });
}
function nv(t) {
  return function() {
    var r = this.__on;
    if (r) {
      for (var i = 0, l = -1, u = r.length, c; i < u; ++i)
        c = r[i], (!t.type || c.type === t.type) && c.name === t.name ? this.removeEventListener(c.type, c.listener, c.options) : r[++l] = c;
      ++l ? r.length = l : delete this.__on;
    }
  };
}
function rv(t, r, i) {
  return function() {
    var l = this.__on, u, c = ev(r);
    if (l) {
      for (var f = 0, h = l.length; f < h; ++f)
        if ((u = l[f]).type === t.type && u.name === t.name) {
          this.removeEventListener(u.type, u.listener, u.options), this.addEventListener(u.type, u.listener = c, u.options = i), u.value = r;
          return;
        }
    }
    this.addEventListener(t.type, c, i), u = { type: t.type, name: t.name, value: r, listener: c, options: i }, l ? l.push(u) : this.__on = [u];
  };
}
function ov(t, r, i) {
  var l = tv(t + ""), u, c = l.length, f;
  if (arguments.length < 2) {
    var h = this.node().__on;
    if (h) {
      for (var p = 0, m = h.length, v; p < m; ++p)
        for (u = 0, v = h[p]; u < c; ++u)
          if ((f = l[u]).type === v.type && f.name === v.name)
            return v.value;
    }
    return;
  }
  for (h = r ? rv : nv, u = 0; u < c; ++u) this.each(h(l[u], r, i));
  return this;
}
function fp(t, r, i) {
  var l = sp(t), u = l.CustomEvent;
  typeof u == "function" ? u = new u(r, i) : (u = l.document.createEvent("Event"), i ? (u.initEvent(r, i.bubbles, i.cancelable), u.detail = i.detail) : u.initEvent(r, !1, !1)), t.dispatchEvent(u);
}
function iv(t, r) {
  return function() {
    return fp(this, t, r);
  };
}
function sv(t, r) {
  return function() {
    return fp(this, t, r.apply(this, arguments));
  };
}
function lv(t, r) {
  return this.each((typeof r == "function" ? sv : iv)(t, r));
}
function* uv() {
  for (var t = this._groups, r = 0, i = t.length; r < i; ++r)
    for (var l = t[r], u = 0, c = l.length, f; u < c; ++u)
      (f = l[u]) && (yield f);
}
var dp = [null];
function xt(t, r) {
  this._groups = t, this._parents = r;
}
function di() {
  return new xt([[document.documentElement]], dp);
}
function av() {
  return this;
}
xt.prototype = di.prototype = {
  constructor: xt,
  select: $0,
  selectAll: F0,
  selectChild: W0,
  selectChildren: X0,
  filter: Q0,
  data: ey,
  enter: K0,
  exit: ny,
  join: ry,
  merge: oy,
  selection: av,
  order: iy,
  sort: sy,
  call: uy,
  nodes: ay,
  node: cy,
  size: fy,
  empty: dy,
  each: hy,
  attr: xy,
  style: ky,
  property: Py,
  classed: Ly,
  text: Dy,
  html: Hy,
  raise: By,
  lower: Uy,
  append: Yy,
  insert: Xy,
  remove: Ky,
  clone: qy,
  datum: Jy,
  on: ov,
  dispatch: lv,
  [Symbol.iterator]: uv
};
function wt(t) {
  return typeof t == "string" ? new xt([[document.querySelector(t)]], [document.documentElement]) : new xt([[t]], dp);
}
function cv(t) {
  let r;
  for (; r = t.sourceEvent; ) t = r;
  return t;
}
function Dt(t, r) {
  if (t = cv(t), r === void 0 && (r = t.currentTarget), r) {
    var i = r.ownerSVGElement || r;
    if (i.createSVGPoint) {
      var l = i.createSVGPoint();
      return l.x = t.clientX, l.y = t.clientY, l = l.matrixTransform(r.getScreenCTM().inverse()), [l.x, l.y];
    }
    if (r.getBoundingClientRect) {
      var u = r.getBoundingClientRect();
      return [t.clientX - u.left - r.clientLeft, t.clientY - u.top - r.clientTop];
    }
  }
  return [t.pageX, t.pageY];
}
const fv = { passive: !1 }, ti = { capture: !0, passive: !1 };
function Sa(t) {
  t.stopImmediatePropagation();
}
function Xr(t) {
  t.preventDefault(), t.stopImmediatePropagation();
}
function hp(t) {
  var r = t.document.documentElement, i = wt(t).on("dragstart.drag", Xr, ti);
  "onselectstart" in r ? i.on("selectstart.drag", Xr, ti) : (r.__noselect = r.style.MozUserSelect, r.style.MozUserSelect = "none");
}
function pp(t, r) {
  var i = t.document.documentElement, l = wt(t).on("dragstart.drag", null);
  r && (l.on("click.drag", Xr, ti), setTimeout(function() {
    l.on("click.drag", null);
  }, 0)), "onselectstart" in i ? l.on("selectstart.drag", null) : (i.style.MozUserSelect = i.__noselect, delete i.__noselect);
}
const Os = (t) => () => t;
function $a(t, {
  sourceEvent: r,
  subject: i,
  target: l,
  identifier: u,
  active: c,
  x: f,
  y: h,
  dx: p,
  dy: m,
  dispatch: v
}) {
  Object.defineProperties(this, {
    type: { value: t, enumerable: !0, configurable: !0 },
    sourceEvent: { value: r, enumerable: !0, configurable: !0 },
    subject: { value: i, enumerable: !0, configurable: !0 },
    target: { value: l, enumerable: !0, configurable: !0 },
    identifier: { value: u, enumerable: !0, configurable: !0 },
    active: { value: c, enumerable: !0, configurable: !0 },
    x: { value: f, enumerable: !0, configurable: !0 },
    y: { value: h, enumerable: !0, configurable: !0 },
    dx: { value: p, enumerable: !0, configurable: !0 },
    dy: { value: m, enumerable: !0, configurable: !0 },
    _: { value: v }
  });
}
$a.prototype.on = function() {
  var t = this._.on.apply(this._, arguments);
  return t === this._ ? this : t;
};
function dv(t) {
  return !t.ctrlKey && !t.button;
}
function hv() {
  return this.parentNode;
}
function pv(t, r) {
  return r ?? { x: t.x, y: t.y };
}
function gv() {
  return navigator.maxTouchPoints || "ontouchstart" in this;
}
function gp() {
  var t = dv, r = hv, i = pv, l = gv, u = {}, c = dl("start", "drag", "end"), f = 0, h, p, m, v, g = 0;
  function y(E) {
    E.on("mousedown.drag", x).filter(l).on("touchstart.drag", N).on("touchmove.drag", k, fv).on("touchend.drag touchcancel.drag", $).style("touch-action", "none").style("-webkit-tap-highlight-color", "rgba(0,0,0,0)");
  }
  function x(E, D) {
    if (!(v || !t.call(this, E, D))) {
      var j = _(this, r.call(this, E, D), E, D, "mouse");
      j && (wt(E.view).on("mousemove.drag", S, ti).on("mouseup.drag", M, ti), hp(E.view), Sa(E), m = !1, h = E.clientX, p = E.clientY, j("start", E));
    }
  }
  function S(E) {
    if (Xr(E), !m) {
      var D = E.clientX - h, j = E.clientY - p;
      m = D * D + j * j > g;
    }
    u.mouse("drag", E);
  }
  function M(E) {
    wt(E.view).on("mousemove.drag mouseup.drag", null), pp(E.view, m), Xr(E), u.mouse("end", E);
  }
  function N(E, D) {
    if (t.call(this, E, D)) {
      var j = E.changedTouches, H = r.call(this, E, D), b = j.length, K, ee;
      for (K = 0; K < b; ++K)
        (ee = _(this, H, E, D, j[K].identifier, j[K])) && (Sa(E), ee("start", E, j[K]));
    }
  }
  function k(E) {
    var D = E.changedTouches, j = D.length, H, b;
    for (H = 0; H < j; ++H)
      (b = u[D[H].identifier]) && (Xr(E), b("drag", E, D[H]));
  }
  function $(E) {
    var D = E.changedTouches, j = D.length, H, b;
    for (v && clearTimeout(v), v = setTimeout(function() {
      v = null;
    }, 500), H = 0; H < j; ++H)
      (b = u[D[H].identifier]) && (Sa(E), b("end", E, D[H]));
  }
  function _(E, D, j, H, b, K) {
    var ee = c.copy(), U = Dt(K || j, D), G, Q, z;
    if ((z = i.call(E, new $a("beforestart", {
      sourceEvent: j,
      target: y,
      identifier: b,
      active: f,
      x: U[0],
      y: U[1],
      dx: 0,
      dy: 0,
      dispatch: ee
    }), H)) != null)
      return G = z.x - U[0] || 0, Q = z.y - U[1] || 0, function B(F, W, T) {
        var L = U, V;
        switch (F) {
          case "start":
            u[b] = B, V = f++;
            break;
          case "end":
            delete u[b], --f;
          // falls through
          case "drag":
            U = Dt(T || W, D), V = f;
            break;
        }
        ee.call(
          F,
          E,
          new $a(F, {
            sourceEvent: W,
            subject: z,
            target: y,
            identifier: b,
            active: V,
            x: U[0] + G,
            y: U[1] + Q,
            dx: U[0] - L[0],
            dy: U[1] - L[1],
            dispatch: ee
          }),
          H
        );
      };
  }
  return y.filter = function(E) {
    return arguments.length ? (t = typeof E == "function" ? E : Os(!!E), y) : t;
  }, y.container = function(E) {
    return arguments.length ? (r = typeof E == "function" ? E : Os(E), y) : r;
  }, y.subject = function(E) {
    return arguments.length ? (i = typeof E == "function" ? E : Os(E), y) : i;
  }, y.touchable = function(E) {
    return arguments.length ? (l = typeof E == "function" ? E : Os(!!E), y) : l;
  }, y.on = function() {
    var E = c.on.apply(c, arguments);
    return E === c ? y : E;
  }, y.clickDistance = function(E) {
    return arguments.length ? (g = (E = +E) * E, y) : Math.sqrt(g);
  }, y;
}
function Za(t, r, i) {
  t.prototype = r.prototype = i, i.constructor = t;
}
function mp(t, r) {
  var i = Object.create(t.prototype);
  for (var l in r) i[l] = r[l];
  return i;
}
function hi() {
}
var ni = 0.7, el = 1 / ni, Qr = "\\s*([+-]?\\d+)\\s*", ri = "\\s*([+-]?(?:\\d*\\.)?\\d+(?:[eE][+-]?\\d+)?)\\s*", Qt = "\\s*([+-]?(?:\\d*\\.)?\\d+(?:[eE][+-]?\\d+)?)%\\s*", mv = /^#([0-9a-f]{3,8})$/, yv = new RegExp(`^rgb\\(${Qr},${Qr},${Qr}\\)$`), vv = new RegExp(`^rgb\\(${Qt},${Qt},${Qt}\\)$`), wv = new RegExp(`^rgba\\(${Qr},${Qr},${Qr},${ri}\\)$`), xv = new RegExp(`^rgba\\(${Qt},${Qt},${Qt},${ri}\\)$`), Sv = new RegExp(`^hsl\\(${ri},${Qt},${Qt}\\)$`), _v = new RegExp(`^hsla\\(${ri},${Qt},${Qt},${ri}\\)$`), Xd = {
  aliceblue: 15792383,
  antiquewhite: 16444375,
  aqua: 65535,
  aquamarine: 8388564,
  azure: 15794175,
  beige: 16119260,
  bisque: 16770244,
  black: 0,
  blanchedalmond: 16772045,
  blue: 255,
  blueviolet: 9055202,
  brown: 10824234,
  burlywood: 14596231,
  cadetblue: 6266528,
  chartreuse: 8388352,
  chocolate: 13789470,
  coral: 16744272,
  cornflowerblue: 6591981,
  cornsilk: 16775388,
  crimson: 14423100,
  cyan: 65535,
  darkblue: 139,
  darkcyan: 35723,
  darkgoldenrod: 12092939,
  darkgray: 11119017,
  darkgreen: 25600,
  darkgrey: 11119017,
  darkkhaki: 12433259,
  darkmagenta: 9109643,
  darkolivegreen: 5597999,
  darkorange: 16747520,
  darkorchid: 10040012,
  darkred: 9109504,
  darksalmon: 15308410,
  darkseagreen: 9419919,
  darkslateblue: 4734347,
  darkslategray: 3100495,
  darkslategrey: 3100495,
  darkturquoise: 52945,
  darkviolet: 9699539,
  deeppink: 16716947,
  deepskyblue: 49151,
  dimgray: 6908265,
  dimgrey: 6908265,
  dodgerblue: 2003199,
  firebrick: 11674146,
  floralwhite: 16775920,
  forestgreen: 2263842,
  fuchsia: 16711935,
  gainsboro: 14474460,
  ghostwhite: 16316671,
  gold: 16766720,
  goldenrod: 14329120,
  gray: 8421504,
  green: 32768,
  greenyellow: 11403055,
  grey: 8421504,
  honeydew: 15794160,
  hotpink: 16738740,
  indianred: 13458524,
  indigo: 4915330,
  ivory: 16777200,
  khaki: 15787660,
  lavender: 15132410,
  lavenderblush: 16773365,
  lawngreen: 8190976,
  lemonchiffon: 16775885,
  lightblue: 11393254,
  lightcoral: 15761536,
  lightcyan: 14745599,
  lightgoldenrodyellow: 16448210,
  lightgray: 13882323,
  lightgreen: 9498256,
  lightgrey: 13882323,
  lightpink: 16758465,
  lightsalmon: 16752762,
  lightseagreen: 2142890,
  lightskyblue: 8900346,
  lightslategray: 7833753,
  lightslategrey: 7833753,
  lightsteelblue: 11584734,
  lightyellow: 16777184,
  lime: 65280,
  limegreen: 3329330,
  linen: 16445670,
  magenta: 16711935,
  maroon: 8388608,
  mediumaquamarine: 6737322,
  mediumblue: 205,
  mediumorchid: 12211667,
  mediumpurple: 9662683,
  mediumseagreen: 3978097,
  mediumslateblue: 8087790,
  mediumspringgreen: 64154,
  mediumturquoise: 4772300,
  mediumvioletred: 13047173,
  midnightblue: 1644912,
  mintcream: 16121850,
  mistyrose: 16770273,
  moccasin: 16770229,
  navajowhite: 16768685,
  navy: 128,
  oldlace: 16643558,
  olive: 8421376,
  olivedrab: 7048739,
  orange: 16753920,
  orangered: 16729344,
  orchid: 14315734,
  palegoldenrod: 15657130,
  palegreen: 10025880,
  paleturquoise: 11529966,
  palevioletred: 14381203,
  papayawhip: 16773077,
  peachpuff: 16767673,
  peru: 13468991,
  pink: 16761035,
  plum: 14524637,
  powderblue: 11591910,
  purple: 8388736,
  rebeccapurple: 6697881,
  red: 16711680,
  rosybrown: 12357519,
  royalblue: 4286945,
  saddlebrown: 9127187,
  salmon: 16416882,
  sandybrown: 16032864,
  seagreen: 3050327,
  seashell: 16774638,
  sienna: 10506797,
  silver: 12632256,
  skyblue: 8900331,
  slateblue: 6970061,
  slategray: 7372944,
  slategrey: 7372944,
  snow: 16775930,
  springgreen: 65407,
  steelblue: 4620980,
  tan: 13808780,
  teal: 32896,
  thistle: 14204888,
  tomato: 16737095,
  turquoise: 4251856,
  violet: 15631086,
  wheat: 16113331,
  white: 16777215,
  whitesmoke: 16119285,
  yellow: 16776960,
  yellowgreen: 10145074
};
Za(hi, cr, {
  copy(t) {
    return Object.assign(new this.constructor(), this, t);
  },
  displayable() {
    return this.rgb().displayable();
  },
  hex: Qd,
  // Deprecated! Use color.formatHex.
  formatHex: Qd,
  formatHex8: Ev,
  formatHsl: kv,
  formatRgb: Kd,
  toString: Kd
});
function Qd() {
  return this.rgb().formatHex();
}
function Ev() {
  return this.rgb().formatHex8();
}
function kv() {
  return yp(this).formatHsl();
}
function Kd() {
  return this.rgb().formatRgb();
}
function cr(t) {
  var r, i;
  return t = (t + "").trim().toLowerCase(), (r = mv.exec(t)) ? (i = r[1].length, r = parseInt(r[1], 16), i === 6 ? Gd(r) : i === 3 ? new ft(r >> 8 & 15 | r >> 4 & 240, r >> 4 & 15 | r & 240, (r & 15) << 4 | r & 15, 1) : i === 8 ? Fs(r >> 24 & 255, r >> 16 & 255, r >> 8 & 255, (r & 255) / 255) : i === 4 ? Fs(r >> 12 & 15 | r >> 8 & 240, r >> 8 & 15 | r >> 4 & 240, r >> 4 & 15 | r & 240, ((r & 15) << 4 | r & 15) / 255) : null) : (r = yv.exec(t)) ? new ft(r[1], r[2], r[3], 1) : (r = vv.exec(t)) ? new ft(r[1] * 255 / 100, r[2] * 255 / 100, r[3] * 255 / 100, 1) : (r = wv.exec(t)) ? Fs(r[1], r[2], r[3], r[4]) : (r = xv.exec(t)) ? Fs(r[1] * 255 / 100, r[2] * 255 / 100, r[3] * 255 / 100, r[4]) : (r = Sv.exec(t)) ? Jd(r[1], r[2] / 100, r[3] / 100, 1) : (r = _v.exec(t)) ? Jd(r[1], r[2] / 100, r[3] / 100, r[4]) : Xd.hasOwnProperty(t) ? Gd(Xd[t]) : t === "transparent" ? new ft(NaN, NaN, NaN, 0) : null;
}
function Gd(t) {
  return new ft(t >> 16 & 255, t >> 8 & 255, t & 255, 1);
}
function Fs(t, r, i, l) {
  return l <= 0 && (t = r = i = NaN), new ft(t, r, i, l);
}
function Nv(t) {
  return t instanceof hi || (t = cr(t)), t ? (t = t.rgb(), new ft(t.r, t.g, t.b, t.opacity)) : new ft();
}
function Da(t, r, i, l) {
  return arguments.length === 1 ? Nv(t) : new ft(t, r, i, l ?? 1);
}
function ft(t, r, i, l) {
  this.r = +t, this.g = +r, this.b = +i, this.opacity = +l;
}
Za(ft, Da, mp(hi, {
  brighter(t) {
    return t = t == null ? el : Math.pow(el, t), new ft(this.r * t, this.g * t, this.b * t, this.opacity);
  },
  darker(t) {
    return t = t == null ? ni : Math.pow(ni, t), new ft(this.r * t, this.g * t, this.b * t, this.opacity);
  },
  rgb() {
    return this;
  },
  clamp() {
    return new ft(ur(this.r), ur(this.g), ur(this.b), tl(this.opacity));
  },
  displayable() {
    return -0.5 <= this.r && this.r < 255.5 && -0.5 <= this.g && this.g < 255.5 && -0.5 <= this.b && this.b < 255.5 && 0 <= this.opacity && this.opacity <= 1;
  },
  hex: Zd,
  // Deprecated! Use color.formatHex.
  formatHex: Zd,
  formatHex8: Cv,
  formatRgb: qd,
  toString: qd
}));
function Zd() {
  return `#${lr(this.r)}${lr(this.g)}${lr(this.b)}`;
}
function Cv() {
  return `#${lr(this.r)}${lr(this.g)}${lr(this.b)}${lr((isNaN(this.opacity) ? 1 : this.opacity) * 255)}`;
}
function qd() {
  const t = tl(this.opacity);
  return `${t === 1 ? "rgb(" : "rgba("}${ur(this.r)}, ${ur(this.g)}, ${ur(this.b)}${t === 1 ? ")" : `, ${t})`}`;
}
function tl(t) {
  return isNaN(t) ? 1 : Math.max(0, Math.min(1, t));
}
function ur(t) {
  return Math.max(0, Math.min(255, Math.round(t) || 0));
}
function lr(t) {
  return t = ur(t), (t < 16 ? "0" : "") + t.toString(16);
}
function Jd(t, r, i, l) {
  return l <= 0 ? t = r = i = NaN : i <= 0 || i >= 1 ? t = r = NaN : r <= 0 && (t = NaN), new jt(t, r, i, l);
}
function yp(t) {
  if (t instanceof jt) return new jt(t.h, t.s, t.l, t.opacity);
  if (t instanceof hi || (t = cr(t)), !t) return new jt();
  if (t instanceof jt) return t;
  t = t.rgb();
  var r = t.r / 255, i = t.g / 255, l = t.b / 255, u = Math.min(r, i, l), c = Math.max(r, i, l), f = NaN, h = c - u, p = (c + u) / 2;
  return h ? (r === c ? f = (i - l) / h + (i < l) * 6 : i === c ? f = (l - r) / h + 2 : f = (r - i) / h + 4, h /= p < 0.5 ? c + u : 2 - c - u, f *= 60) : h = p > 0 && p < 1 ? 0 : f, new jt(f, h, p, t.opacity);
}
function Mv(t, r, i, l) {
  return arguments.length === 1 ? yp(t) : new jt(t, r, i, l ?? 1);
}
function jt(t, r, i, l) {
  this.h = +t, this.s = +r, this.l = +i, this.opacity = +l;
}
Za(jt, Mv, mp(hi, {
  brighter(t) {
    return t = t == null ? el : Math.pow(el, t), new jt(this.h, this.s, this.l * t, this.opacity);
  },
  darker(t) {
    return t = t == null ? ni : Math.pow(ni, t), new jt(this.h, this.s, this.l * t, this.opacity);
  },
  rgb() {
    var t = this.h % 360 + (this.h < 0) * 360, r = isNaN(t) || isNaN(this.s) ? 0 : this.s, i = this.l, l = i + (i < 0.5 ? i : 1 - i) * r, u = 2 * i - l;
    return new ft(
      _a(t >= 240 ? t - 240 : t + 120, u, l),
      _a(t, u, l),
      _a(t < 120 ? t + 240 : t - 120, u, l),
      this.opacity
    );
  },
  clamp() {
    return new jt(eh(this.h), Hs(this.s), Hs(this.l), tl(this.opacity));
  },
  displayable() {
    return (0 <= this.s && this.s <= 1 || isNaN(this.s)) && 0 <= this.l && this.l <= 1 && 0 <= this.opacity && this.opacity <= 1;
  },
  formatHsl() {
    const t = tl(this.opacity);
    return `${t === 1 ? "hsl(" : "hsla("}${eh(this.h)}, ${Hs(this.s) * 100}%, ${Hs(this.l) * 100}%${t === 1 ? ")" : `, ${t})`}`;
  }
}));
function eh(t) {
  return t = (t || 0) % 360, t < 0 ? t + 360 : t;
}
function Hs(t) {
  return Math.max(0, Math.min(1, t || 0));
}
function _a(t, r, i) {
  return (t < 60 ? r + (i - r) * t / 60 : t < 180 ? i : t < 240 ? r + (i - r) * (240 - t) / 60 : r) * 255;
}
const qa = (t) => () => t;
function Pv(t, r) {
  return function(i) {
    return t + i * r;
  };
}
function Iv(t, r, i) {
  return t = Math.pow(t, i), r = Math.pow(r, i) - t, i = 1 / i, function(l) {
    return Math.pow(t + l * r, i);
  };
}
function zv(t) {
  return (t = +t) == 1 ? vp : function(r, i) {
    return i - r ? Iv(r, i, t) : qa(isNaN(r) ? i : r);
  };
}
function vp(t, r) {
  var i = r - t;
  return i ? Pv(t, i) : qa(isNaN(t) ? r : t);
}
const nl = (function t(r) {
  var i = zv(r);
  function l(u, c) {
    var f = i((u = Da(u)).r, (c = Da(c)).r), h = i(u.g, c.g), p = i(u.b, c.b), m = vp(u.opacity, c.opacity);
    return function(v) {
      return u.r = f(v), u.g = h(v), u.b = p(v), u.opacity = m(v), u + "";
    };
  }
  return l.gamma = t, l;
})(1);
function Tv(t, r) {
  r || (r = []);
  var i = t ? Math.min(r.length, t.length) : 0, l = r.slice(), u;
  return function(c) {
    for (u = 0; u < i; ++u) l[u] = t[u] * (1 - c) + r[u] * c;
    return l;
  };
}
function Lv(t) {
  return ArrayBuffer.isView(t) && !(t instanceof DataView);
}
function Av(t, r) {
  var i = r ? r.length : 0, l = t ? Math.min(i, t.length) : 0, u = new Array(l), c = new Array(i), f;
  for (f = 0; f < l; ++f) u[f] = Jo(t[f], r[f]);
  for (; f < i; ++f) c[f] = r[f];
  return function(h) {
    for (f = 0; f < l; ++f) c[f] = u[f](h);
    return c;
  };
}
function Rv(t, r) {
  var i = /* @__PURE__ */ new Date();
  return t = +t, r = +r, function(l) {
    return i.setTime(t * (1 - l) + r * l), i;
  };
}
function Xt(t, r) {
  return t = +t, r = +r, function(i) {
    return t * (1 - i) + r * i;
  };
}
function $v(t, r) {
  var i = {}, l = {}, u;
  (t === null || typeof t != "object") && (t = {}), (r === null || typeof r != "object") && (r = {});
  for (u in r)
    u in t ? i[u] = Jo(t[u], r[u]) : l[u] = r[u];
  return function(c) {
    for (u in i) l[u] = i[u](c);
    return l;
  };
}
var ja = /[-+]?(?:\d+\.?\d*|\.?\d+)(?:[eE][-+]?\d+)?/g, Ea = new RegExp(ja.source, "g");
function Dv(t) {
  return function() {
    return t;
  };
}
function jv(t) {
  return function(r) {
    return t(r) + "";
  };
}
function wp(t, r) {
  var i = ja.lastIndex = Ea.lastIndex = 0, l, u, c, f = -1, h = [], p = [];
  for (t = t + "", r = r + ""; (l = ja.exec(t)) && (u = Ea.exec(r)); )
    (c = u.index) > i && (c = r.slice(i, c), h[f] ? h[f] += c : h[++f] = c), (l = l[0]) === (u = u[0]) ? h[f] ? h[f] += u : h[++f] = u : (h[++f] = null, p.push({ i: f, x: Xt(l, u) })), i = Ea.lastIndex;
  return i < r.length && (c = r.slice(i), h[f] ? h[f] += c : h[++f] = c), h.length < 2 ? p[0] ? jv(p[0].x) : Dv(r) : (r = p.length, function(m) {
    for (var v = 0, g; v < r; ++v) h[(g = p[v]).i] = g.x(m);
    return h.join("");
  });
}
function Jo(t, r) {
  var i = typeof r, l;
  return r == null || i === "boolean" ? qa(r) : (i === "number" ? Xt : i === "string" ? (l = cr(r)) ? (r = l, nl) : wp : r instanceof cr ? nl : r instanceof Date ? Rv : Lv(r) ? Tv : Array.isArray(r) ? Av : typeof r.valueOf != "function" && typeof r.toString != "function" || isNaN(r) ? $v : Xt)(t, r);
}
var th = 180 / Math.PI, Oa = {
  translateX: 0,
  translateY: 0,
  rotate: 0,
  skewX: 0,
  scaleX: 1,
  scaleY: 1
};
function xp(t, r, i, l, u, c) {
  var f, h, p;
  return (f = Math.sqrt(t * t + r * r)) && (t /= f, r /= f), (p = t * i + r * l) && (i -= t * p, l -= r * p), (h = Math.sqrt(i * i + l * l)) && (i /= h, l /= h, p /= h), t * l < r * i && (t = -t, r = -r, p = -p, f = -f), {
    translateX: u,
    translateY: c,
    rotate: Math.atan2(r, t) * th,
    skewX: Math.atan(p) * th,
    scaleX: f,
    scaleY: h
  };
}
var Vs;
function Ov(t) {
  const r = new (typeof DOMMatrix == "function" ? DOMMatrix : WebKitCSSMatrix)(t + "");
  return r.isIdentity ? Oa : xp(r.a, r.b, r.c, r.d, r.e, r.f);
}
function Fv(t) {
  return t == null || (Vs || (Vs = document.createElementNS("http://www.w3.org/2000/svg", "g")), Vs.setAttribute("transform", t), !(t = Vs.transform.baseVal.consolidate())) ? Oa : (t = t.matrix, xp(t.a, t.b, t.c, t.d, t.e, t.f));
}
function Sp(t, r, i, l) {
  function u(m) {
    return m.length ? m.pop() + " " : "";
  }
  function c(m, v, g, y, x, S) {
    if (m !== g || v !== y) {
      var M = x.push("translate(", null, r, null, i);
      S.push({ i: M - 4, x: Xt(m, g) }, { i: M - 2, x: Xt(v, y) });
    } else (g || y) && x.push("translate(" + g + r + y + i);
  }
  function f(m, v, g, y) {
    m !== v ? (m - v > 180 ? v += 360 : v - m > 180 && (m += 360), y.push({ i: g.push(u(g) + "rotate(", null, l) - 2, x: Xt(m, v) })) : v && g.push(u(g) + "rotate(" + v + l);
  }
  function h(m, v, g, y) {
    m !== v ? y.push({ i: g.push(u(g) + "skewX(", null, l) - 2, x: Xt(m, v) }) : v && g.push(u(g) + "skewX(" + v + l);
  }
  function p(m, v, g, y, x, S) {
    if (m !== g || v !== y) {
      var M = x.push(u(x) + "scale(", null, ",", null, ")");
      S.push({ i: M - 4, x: Xt(m, g) }, { i: M - 2, x: Xt(v, y) });
    } else (g !== 1 || y !== 1) && x.push(u(x) + "scale(" + g + "," + y + ")");
  }
  return function(m, v) {
    var g = [], y = [];
    return m = t(m), v = t(v), c(m.translateX, m.translateY, v.translateX, v.translateY, g, y), f(m.rotate, v.rotate, g, y), h(m.skewX, v.skewX, g, y), p(m.scaleX, m.scaleY, v.scaleX, v.scaleY, g, y), m = v = null, function(x) {
      for (var S = -1, M = y.length, N; ++S < M; ) g[(N = y[S]).i] = N.x(x);
      return g.join("");
    };
  };
}
var Hv = Sp(Ov, "px, ", "px)", "deg)"), Vv = Sp(Fv, ", ", ")", ")"), Bv = 1e-12;
function nh(t) {
  return ((t = Math.exp(t)) + 1 / t) / 2;
}
function Wv(t) {
  return ((t = Math.exp(t)) - 1 / t) / 2;
}
function Uv(t) {
  return ((t = Math.exp(2 * t)) - 1) / (t + 1);
}
const Ks = (function t(r, i, l) {
  function u(c, f) {
    var h = c[0], p = c[1], m = c[2], v = f[0], g = f[1], y = f[2], x = v - h, S = g - p, M = x * x + S * S, N, k;
    if (M < Bv)
      k = Math.log(y / m) / r, N = function(H) {
        return [
          h + H * x,
          p + H * S,
          m * Math.exp(r * H * k)
        ];
      };
    else {
      var $ = Math.sqrt(M), _ = (y * y - m * m + l * M) / (2 * m * i * $), E = (y * y - m * m - l * M) / (2 * y * i * $), D = Math.log(Math.sqrt(_ * _ + 1) - _), j = Math.log(Math.sqrt(E * E + 1) - E);
      k = (j - D) / r, N = function(H) {
        var b = H * k, K = nh(D), ee = m / (i * $) * (K * Uv(r * b + D) - Wv(D));
        return [
          h + ee * x,
          p + ee * S,
          m * K / nh(r * b + D)
        ];
      };
    }
    return N.duration = k * 1e3 * r / Math.SQRT2, N;
  }
  return u.rho = function(c) {
    var f = Math.max(1e-3, +c), h = f * f, p = h * h;
    return t(f, h, p);
  }, u;
})(Math.SQRT2, 2, 4);
var Gr = 0, Zo = 0, Ko = 0, _p = 1e3, rl, qo, ol = 0, fr = 0, pl = 0, oi = typeof performance == "object" && performance.now ? performance : Date, Ep = typeof window == "object" && window.requestAnimationFrame ? window.requestAnimationFrame.bind(window) : function(t) {
  setTimeout(t, 17);
};
function Ja() {
  return fr || (Ep(Yv), fr = oi.now() + pl);
}
function Yv() {
  fr = 0;
}
function il() {
  this._call = this._time = this._next = null;
}
il.prototype = kp.prototype = {
  constructor: il,
  restart: function(t, r, i) {
    if (typeof t != "function") throw new TypeError("callback is not a function");
    i = (i == null ? Ja() : +i) + (r == null ? 0 : +r), !this._next && qo !== this && (qo ? qo._next = this : rl = this, qo = this), this._call = t, this._time = i, Fa();
  },
  stop: function() {
    this._call && (this._call = null, this._time = 1 / 0, Fa());
  }
};
function kp(t, r, i) {
  var l = new il();
  return l.restart(t, r, i), l;
}
function bv() {
  Ja(), ++Gr;
  for (var t = rl, r; t; )
    (r = fr - t._time) >= 0 && t._call.call(void 0, r), t = t._next;
  --Gr;
}
function rh() {
  fr = (ol = oi.now()) + pl, Gr = Zo = 0;
  try {
    bv();
  } finally {
    Gr = 0, Qv(), fr = 0;
  }
}
function Xv() {
  var t = oi.now(), r = t - ol;
  r > _p && (pl -= r, ol = t);
}
function Qv() {
  for (var t, r = rl, i, l = 1 / 0; r; )
    r._call ? (l > r._time && (l = r._time), t = r, r = r._next) : (i = r._next, r._next = null, r = t ? t._next = i : rl = i);
  qo = t, Fa(l);
}
function Fa(t) {
  if (!Gr) {
    Zo && (Zo = clearTimeout(Zo));
    var r = t - fr;
    r > 24 ? (t < 1 / 0 && (Zo = setTimeout(rh, t - oi.now() - pl)), Ko && (Ko = clearInterval(Ko))) : (Ko || (ol = oi.now(), Ko = setInterval(Xv, _p)), Gr = 1, Ep(rh));
  }
}
function oh(t, r, i) {
  var l = new il();
  return r = r == null ? 0 : +r, l.restart((u) => {
    l.stop(), t(u + r);
  }, r, i), l;
}
var Kv = dl("start", "end", "cancel", "interrupt"), Gv = [], Np = 0, ih = 1, Ha = 2, Gs = 3, sh = 4, Va = 5, Zs = 6;
function gl(t, r, i, l, u, c) {
  var f = t.__transition;
  if (!f) t.__transition = {};
  else if (i in f) return;
  Zv(t, i, {
    name: r,
    index: l,
    // For context during callback.
    group: u,
    // For context during callback.
    on: Kv,
    tween: Gv,
    time: c.time,
    delay: c.delay,
    duration: c.duration,
    ease: c.ease,
    timer: null,
    state: Np
  });
}
function ec(t, r) {
  var i = Ht(t, r);
  if (i.state > Np) throw new Error("too late; already scheduled");
  return i;
}
function Gt(t, r) {
  var i = Ht(t, r);
  if (i.state > Gs) throw new Error("too late; already running");
  return i;
}
function Ht(t, r) {
  var i = t.__transition;
  if (!i || !(i = i[r])) throw new Error("transition not found");
  return i;
}
function Zv(t, r, i) {
  var l = t.__transition, u;
  l[r] = i, i.timer = kp(c, 0, i.time);
  function c(m) {
    i.state = ih, i.timer.restart(f, i.delay, i.time), i.delay <= m && f(m - i.delay);
  }
  function f(m) {
    var v, g, y, x;
    if (i.state !== ih) return p();
    for (v in l)
      if (x = l[v], x.name === i.name) {
        if (x.state === Gs) return oh(f);
        x.state === sh ? (x.state = Zs, x.timer.stop(), x.on.call("interrupt", t, t.__data__, x.index, x.group), delete l[v]) : +v < r && (x.state = Zs, x.timer.stop(), x.on.call("cancel", t, t.__data__, x.index, x.group), delete l[v]);
      }
    if (oh(function() {
      i.state === Gs && (i.state = sh, i.timer.restart(h, i.delay, i.time), h(m));
    }), i.state = Ha, i.on.call("start", t, t.__data__, i.index, i.group), i.state === Ha) {
      for (i.state = Gs, u = new Array(y = i.tween.length), v = 0, g = -1; v < y; ++v)
        (x = i.tween[v].value.call(t, t.__data__, i.index, i.group)) && (u[++g] = x);
      u.length = g + 1;
    }
  }
  function h(m) {
    for (var v = m < i.duration ? i.ease.call(null, m / i.duration) : (i.timer.restart(p), i.state = Va, 1), g = -1, y = u.length; ++g < y; )
      u[g].call(t, v);
    i.state === Va && (i.on.call("end", t, t.__data__, i.index, i.group), p());
  }
  function p() {
    i.state = Zs, i.timer.stop(), delete l[r];
    for (var m in l) return;
    delete t.__transition;
  }
}
function qs(t, r) {
  var i = t.__transition, l, u, c = !0, f;
  if (i) {
    r = r == null ? null : r + "";
    for (f in i) {
      if ((l = i[f]).name !== r) {
        c = !1;
        continue;
      }
      u = l.state > Ha && l.state < Va, l.state = Zs, l.timer.stop(), l.on.call(u ? "interrupt" : "cancel", t, t.__data__, l.index, l.group), delete i[f];
    }
    c && delete t.__transition;
  }
}
function qv(t) {
  return this.each(function() {
    qs(this, t);
  });
}
function Jv(t, r) {
  var i, l;
  return function() {
    var u = Gt(this, t), c = u.tween;
    if (c !== i) {
      l = i = c;
      for (var f = 0, h = l.length; f < h; ++f)
        if (l[f].name === r) {
          l = l.slice(), l.splice(f, 1);
          break;
        }
    }
    u.tween = l;
  };
}
function ew(t, r, i) {
  var l, u;
  if (typeof i != "function") throw new Error();
  return function() {
    var c = Gt(this, t), f = c.tween;
    if (f !== l) {
      u = (l = f).slice();
      for (var h = { name: r, value: i }, p = 0, m = u.length; p < m; ++p)
        if (u[p].name === r) {
          u[p] = h;
          break;
        }
      p === m && u.push(h);
    }
    c.tween = u;
  };
}
function tw(t, r) {
  var i = this._id;
  if (t += "", arguments.length < 2) {
    for (var l = Ht(this.node(), i).tween, u = 0, c = l.length, f; u < c; ++u)
      if ((f = l[u]).name === t)
        return f.value;
    return null;
  }
  return this.each((r == null ? Jv : ew)(i, t, r));
}
function tc(t, r, i) {
  var l = t._id;
  return t.each(function() {
    var u = Gt(this, l);
    (u.value || (u.value = {}))[r] = i.apply(this, arguments);
  }), function(u) {
    return Ht(u, l).value[r];
  };
}
function Cp(t, r) {
  var i;
  return (typeof r == "number" ? Xt : r instanceof cr ? nl : (i = cr(r)) ? (r = i, nl) : wp)(t, r);
}
function nw(t) {
  return function() {
    this.removeAttribute(t);
  };
}
function rw(t) {
  return function() {
    this.removeAttributeNS(t.space, t.local);
  };
}
function ow(t, r, i) {
  var l, u = i + "", c;
  return function() {
    var f = this.getAttribute(t);
    return f === u ? null : f === l ? c : c = r(l = f, i);
  };
}
function iw(t, r, i) {
  var l, u = i + "", c;
  return function() {
    var f = this.getAttributeNS(t.space, t.local);
    return f === u ? null : f === l ? c : c = r(l = f, i);
  };
}
function sw(t, r, i) {
  var l, u, c;
  return function() {
    var f, h = i(this), p;
    return h == null ? void this.removeAttribute(t) : (f = this.getAttribute(t), p = h + "", f === p ? null : f === l && p === u ? c : (u = p, c = r(l = f, h)));
  };
}
function lw(t, r, i) {
  var l, u, c;
  return function() {
    var f, h = i(this), p;
    return h == null ? void this.removeAttributeNS(t.space, t.local) : (f = this.getAttributeNS(t.space, t.local), p = h + "", f === p ? null : f === l && p === u ? c : (u = p, c = r(l = f, h)));
  };
}
function uw(t, r) {
  var i = hl(t), l = i === "transform" ? Vv : Cp;
  return this.attrTween(t, typeof r == "function" ? (i.local ? lw : sw)(i, l, tc(this, "attr." + t, r)) : r == null ? (i.local ? rw : nw)(i) : (i.local ? iw : ow)(i, l, r));
}
function aw(t, r) {
  return function(i) {
    this.setAttribute(t, r.call(this, i));
  };
}
function cw(t, r) {
  return function(i) {
    this.setAttributeNS(t.space, t.local, r.call(this, i));
  };
}
function fw(t, r) {
  var i, l;
  function u() {
    var c = r.apply(this, arguments);
    return c !== l && (i = (l = c) && cw(t, c)), i;
  }
  return u._value = r, u;
}
function dw(t, r) {
  var i, l;
  function u() {
    var c = r.apply(this, arguments);
    return c !== l && (i = (l = c) && aw(t, c)), i;
  }
  return u._value = r, u;
}
function hw(t, r) {
  var i = "attr." + t;
  if (arguments.length < 2) return (i = this.tween(i)) && i._value;
  if (r == null) return this.tween(i, null);
  if (typeof r != "function") throw new Error();
  var l = hl(t);
  return this.tween(i, (l.local ? fw : dw)(l, r));
}
function pw(t, r) {
  return function() {
    ec(this, t).delay = +r.apply(this, arguments);
  };
}
function gw(t, r) {
  return r = +r, function() {
    ec(this, t).delay = r;
  };
}
function mw(t) {
  var r = this._id;
  return arguments.length ? this.each((typeof t == "function" ? pw : gw)(r, t)) : Ht(this.node(), r).delay;
}
function yw(t, r) {
  return function() {
    Gt(this, t).duration = +r.apply(this, arguments);
  };
}
function vw(t, r) {
  return r = +r, function() {
    Gt(this, t).duration = r;
  };
}
function ww(t) {
  var r = this._id;
  return arguments.length ? this.each((typeof t == "function" ? yw : vw)(r, t)) : Ht(this.node(), r).duration;
}
function xw(t, r) {
  if (typeof r != "function") throw new Error();
  return function() {
    Gt(this, t).ease = r;
  };
}
function Sw(t) {
  var r = this._id;
  return arguments.length ? this.each(xw(r, t)) : Ht(this.node(), r).ease;
}
function _w(t, r) {
  return function() {
    var i = r.apply(this, arguments);
    if (typeof i != "function") throw new Error();
    Gt(this, t).ease = i;
  };
}
function Ew(t) {
  if (typeof t != "function") throw new Error();
  return this.each(_w(this._id, t));
}
function kw(t) {
  typeof t != "function" && (t = rp(t));
  for (var r = this._groups, i = r.length, l = new Array(i), u = 0; u < i; ++u)
    for (var c = r[u], f = c.length, h = l[u] = [], p, m = 0; m < f; ++m)
      (p = c[m]) && t.call(p, p.__data__, m, c) && h.push(p);
  return new dn(l, this._parents, this._name, this._id);
}
function Nw(t) {
  if (t._id !== this._id) throw new Error();
  for (var r = this._groups, i = t._groups, l = r.length, u = i.length, c = Math.min(l, u), f = new Array(l), h = 0; h < c; ++h)
    for (var p = r[h], m = i[h], v = p.length, g = f[h] = new Array(v), y, x = 0; x < v; ++x)
      (y = p[x] || m[x]) && (g[x] = y);
  for (; h < l; ++h)
    f[h] = r[h];
  return new dn(f, this._parents, this._name, this._id);
}
function Cw(t) {
  return (t + "").trim().split(/^|\s+/).every(function(r) {
    var i = r.indexOf(".");
    return i >= 0 && (r = r.slice(0, i)), !r || r === "start";
  });
}
function Mw(t, r, i) {
  var l, u, c = Cw(r) ? ec : Gt;
  return function() {
    var f = c(this, t), h = f.on;
    h !== l && (u = (l = h).copy()).on(r, i), f.on = u;
  };
}
function Pw(t, r) {
  var i = this._id;
  return arguments.length < 2 ? Ht(this.node(), i).on.on(t) : this.each(Mw(i, t, r));
}
function Iw(t) {
  return function() {
    var r = this.parentNode;
    for (var i in this.__transition) if (+i !== t) return;
    r && r.removeChild(this);
  };
}
function zw() {
  return this.on("end.remove", Iw(this._id));
}
function Tw(t) {
  var r = this._name, i = this._id;
  typeof t != "function" && (t = Ka(t));
  for (var l = this._groups, u = l.length, c = new Array(u), f = 0; f < u; ++f)
    for (var h = l[f], p = h.length, m = c[f] = new Array(p), v, g, y = 0; y < p; ++y)
      (v = h[y]) && (g = t.call(v, v.__data__, y, h)) && ("__data__" in v && (g.__data__ = v.__data__), m[y] = g, gl(m[y], r, i, y, m, Ht(v, i)));
  return new dn(c, this._parents, r, i);
}
function Lw(t) {
  var r = this._name, i = this._id;
  typeof t != "function" && (t = np(t));
  for (var l = this._groups, u = l.length, c = [], f = [], h = 0; h < u; ++h)
    for (var p = l[h], m = p.length, v, g = 0; g < m; ++g)
      if (v = p[g]) {
        for (var y = t.call(v, v.__data__, g, p), x, S = Ht(v, i), M = 0, N = y.length; M < N; ++M)
          (x = y[M]) && gl(x, r, i, M, y, S);
        c.push(y), f.push(v);
      }
  return new dn(c, f, r, i);
}
var Aw = di.prototype.constructor;
function Rw() {
  return new Aw(this._groups, this._parents);
}
function $w(t, r) {
  var i, l, u;
  return function() {
    var c = Kr(this, t), f = (this.style.removeProperty(t), Kr(this, t));
    return c === f ? null : c === i && f === l ? u : u = r(i = c, l = f);
  };
}
function Mp(t) {
  return function() {
    this.style.removeProperty(t);
  };
}
function Dw(t, r, i) {
  var l, u = i + "", c;
  return function() {
    var f = Kr(this, t);
    return f === u ? null : f === l ? c : c = r(l = f, i);
  };
}
function jw(t, r, i) {
  var l, u, c;
  return function() {
    var f = Kr(this, t), h = i(this), p = h + "";
    return h == null && (p = h = (this.style.removeProperty(t), Kr(this, t))), f === p ? null : f === l && p === u ? c : (u = p, c = r(l = f, h));
  };
}
function Ow(t, r) {
  var i, l, u, c = "style." + r, f = "end." + c, h;
  return function() {
    var p = Gt(this, t), m = p.on, v = p.value[c] == null ? h || (h = Mp(r)) : void 0;
    (m !== i || u !== v) && (l = (i = m).copy()).on(f, u = v), p.on = l;
  };
}
function Fw(t, r, i) {
  var l = (t += "") == "transform" ? Hv : Cp;
  return r == null ? this.styleTween(t, $w(t, l)).on("end.style." + t, Mp(t)) : typeof r == "function" ? this.styleTween(t, jw(t, l, tc(this, "style." + t, r))).each(Ow(this._id, t)) : this.styleTween(t, Dw(t, l, r), i).on("end.style." + t, null);
}
function Hw(t, r, i) {
  return function(l) {
    this.style.setProperty(t, r.call(this, l), i);
  };
}
function Vw(t, r, i) {
  var l, u;
  function c() {
    var f = r.apply(this, arguments);
    return f !== u && (l = (u = f) && Hw(t, f, i)), l;
  }
  return c._value = r, c;
}
function Bw(t, r, i) {
  var l = "style." + (t += "");
  if (arguments.length < 2) return (l = this.tween(l)) && l._value;
  if (r == null) return this.tween(l, null);
  if (typeof r != "function") throw new Error();
  return this.tween(l, Vw(t, r, i ?? ""));
}
function Ww(t) {
  return function() {
    this.textContent = t;
  };
}
function Uw(t) {
  return function() {
    var r = t(this);
    this.textContent = r ?? "";
  };
}
function Yw(t) {
  return this.tween("text", typeof t == "function" ? Uw(tc(this, "text", t)) : Ww(t == null ? "" : t + ""));
}
function bw(t) {
  return function(r) {
    this.textContent = t.call(this, r);
  };
}
function Xw(t) {
  var r, i;
  function l() {
    var u = t.apply(this, arguments);
    return u !== i && (r = (i = u) && bw(u)), r;
  }
  return l._value = t, l;
}
function Qw(t) {
  var r = "text";
  if (arguments.length < 1) return (r = this.tween(r)) && r._value;
  if (t == null) return this.tween(r, null);
  if (typeof t != "function") throw new Error();
  return this.tween(r, Xw(t));
}
function Kw() {
  for (var t = this._name, r = this._id, i = Pp(), l = this._groups, u = l.length, c = 0; c < u; ++c)
    for (var f = l[c], h = f.length, p, m = 0; m < h; ++m)
      if (p = f[m]) {
        var v = Ht(p, r);
        gl(p, t, i, m, f, {
          time: v.time + v.delay + v.duration,
          delay: 0,
          duration: v.duration,
          ease: v.ease
        });
      }
  return new dn(l, this._parents, t, i);
}
function Gw() {
  var t, r, i = this, l = i._id, u = i.size();
  return new Promise(function(c, f) {
    var h = { value: f }, p = { value: function() {
      --u === 0 && c();
    } };
    i.each(function() {
      var m = Gt(this, l), v = m.on;
      v !== t && (r = (t = v).copy(), r._.cancel.push(h), r._.interrupt.push(h), r._.end.push(p)), m.on = r;
    }), u === 0 && c();
  });
}
var Zw = 0;
function dn(t, r, i, l) {
  this._groups = t, this._parents = r, this._name = i, this._id = l;
}
function Pp() {
  return ++Zw;
}
var cn = di.prototype;
dn.prototype = {
  constructor: dn,
  select: Tw,
  selectAll: Lw,
  selectChild: cn.selectChild,
  selectChildren: cn.selectChildren,
  filter: kw,
  merge: Nw,
  selection: Rw,
  transition: Kw,
  call: cn.call,
  nodes: cn.nodes,
  node: cn.node,
  size: cn.size,
  empty: cn.empty,
  each: cn.each,
  on: Pw,
  attr: uw,
  attrTween: hw,
  style: Fw,
  styleTween: Bw,
  text: Yw,
  textTween: Qw,
  remove: zw,
  tween: tw,
  delay: mw,
  duration: ww,
  ease: Sw,
  easeVarying: Ew,
  end: Gw,
  [Symbol.iterator]: cn[Symbol.iterator]
};
function qw(t) {
  return ((t *= 2) <= 1 ? t * t * t : (t -= 2) * t * t + 2) / 2;
}
var Jw = {
  time: null,
  // Set on use.
  delay: 0,
  duration: 250,
  ease: qw
};
function ex(t, r) {
  for (var i; !(i = t.__transition) || !(i = i[r]); )
    if (!(t = t.parentNode))
      throw new Error(`transition ${r} not found`);
  return i;
}
function tx(t) {
  var r, i;
  t instanceof dn ? (r = t._id, t = t._name) : (r = Pp(), (i = Jw).time = Ja(), t = t == null ? null : t + "");
  for (var l = this._groups, u = l.length, c = 0; c < u; ++c)
    for (var f = l[c], h = f.length, p, m = 0; m < h; ++m)
      (p = f[m]) && gl(p, t, r, m, f, i || ex(p, r));
  return new dn(l, this._parents, t, r);
}
di.prototype.interrupt = qv;
di.prototype.transition = tx;
const Bs = (t) => () => t;
function nx(t, {
  sourceEvent: r,
  target: i,
  transform: l,
  dispatch: u
}) {
  Object.defineProperties(this, {
    type: { value: t, enumerable: !0, configurable: !0 },
    sourceEvent: { value: r, enumerable: !0, configurable: !0 },
    target: { value: i, enumerable: !0, configurable: !0 },
    transform: { value: l, enumerable: !0, configurable: !0 },
    _: { value: u }
  });
}
function fn(t, r, i) {
  this.k = t, this.x = r, this.y = i;
}
fn.prototype = {
  constructor: fn,
  scale: function(t) {
    return t === 1 ? this : new fn(this.k * t, this.x, this.y);
  },
  translate: function(t, r) {
    return t === 0 & r === 0 ? this : new fn(this.k, this.x + this.k * t, this.y + this.k * r);
  },
  apply: function(t) {
    return [t[0] * this.k + this.x, t[1] * this.k + this.y];
  },
  applyX: function(t) {
    return t * this.k + this.x;
  },
  applyY: function(t) {
    return t * this.k + this.y;
  },
  invert: function(t) {
    return [(t[0] - this.x) / this.k, (t[1] - this.y) / this.k];
  },
  invertX: function(t) {
    return (t - this.x) / this.k;
  },
  invertY: function(t) {
    return (t - this.y) / this.k;
  },
  rescaleX: function(t) {
    return t.copy().domain(t.range().map(this.invertX, this).map(t.invert, t));
  },
  rescaleY: function(t) {
    return t.copy().domain(t.range().map(this.invertY, this).map(t.invert, t));
  },
  toString: function() {
    return "translate(" + this.x + "," + this.y + ") scale(" + this.k + ")";
  }
};
var ml = new fn(1, 0, 0);
Ip.prototype = fn.prototype;
function Ip(t) {
  for (; !t.__zoom; ) if (!(t = t.parentNode)) return ml;
  return t.__zoom;
}
function ka(t) {
  t.stopImmediatePropagation();
}
function Go(t) {
  t.preventDefault(), t.stopImmediatePropagation();
}
function rx(t) {
  return (!t.ctrlKey || t.type === "wheel") && !t.button;
}
function ox() {
  var t = this;
  return t instanceof SVGElement ? (t = t.ownerSVGElement || t, t.hasAttribute("viewBox") ? (t = t.viewBox.baseVal, [[t.x, t.y], [t.x + t.width, t.y + t.height]]) : [[0, 0], [t.width.baseVal.value, t.height.baseVal.value]]) : [[0, 0], [t.clientWidth, t.clientHeight]];
}
function lh() {
  return this.__zoom || ml;
}
function ix(t) {
  return -t.deltaY * (t.deltaMode === 1 ? 0.05 : t.deltaMode ? 1 : 2e-3) * (t.ctrlKey ? 10 : 1);
}
function sx() {
  return navigator.maxTouchPoints || "ontouchstart" in this;
}
function lx(t, r, i) {
  var l = t.invertX(r[0][0]) - i[0][0], u = t.invertX(r[1][0]) - i[1][0], c = t.invertY(r[0][1]) - i[0][1], f = t.invertY(r[1][1]) - i[1][1];
  return t.translate(
    u > l ? (l + u) / 2 : Math.min(0, l) || Math.max(0, u),
    f > c ? (c + f) / 2 : Math.min(0, c) || Math.max(0, f)
  );
}
function zp() {
  var t = rx, r = ox, i = lx, l = ix, u = sx, c = [0, 1 / 0], f = [[-1 / 0, -1 / 0], [1 / 0, 1 / 0]], h = 250, p = Ks, m = dl("start", "zoom", "end"), v, g, y, x = 500, S = 150, M = 0, N = 10;
  function k(z) {
    z.property("__zoom", lh).on("wheel.zoom", b, { passive: !1 }).on("mousedown.zoom", K).on("dblclick.zoom", ee).filter(u).on("touchstart.zoom", U).on("touchmove.zoom", G).on("touchend.zoom touchcancel.zoom", Q).style("-webkit-tap-highlight-color", "rgba(0,0,0,0)");
  }
  k.transform = function(z, B, F, W) {
    var T = z.selection ? z.selection() : z;
    T.property("__zoom", lh), z !== T ? D(z, B, F, W) : T.interrupt().each(function() {
      j(this, arguments).event(W).start().zoom(null, typeof B == "function" ? B.apply(this, arguments) : B).end();
    });
  }, k.scaleBy = function(z, B, F, W) {
    k.scaleTo(z, function() {
      var T = this.__zoom.k, L = typeof B == "function" ? B.apply(this, arguments) : B;
      return T * L;
    }, F, W);
  }, k.scaleTo = function(z, B, F, W) {
    k.transform(z, function() {
      var T = r.apply(this, arguments), L = this.__zoom, V = F == null ? E(T) : typeof F == "function" ? F.apply(this, arguments) : F, C = L.invert(V), A = typeof B == "function" ? B.apply(this, arguments) : B;
      return i(_($(L, A), V, C), T, f);
    }, F, W);
  }, k.translateBy = function(z, B, F, W) {
    k.transform(z, function() {
      return i(this.__zoom.translate(
        typeof B == "function" ? B.apply(this, arguments) : B,
        typeof F == "function" ? F.apply(this, arguments) : F
      ), r.apply(this, arguments), f);
    }, null, W);
  }, k.translateTo = function(z, B, F, W, T) {
    k.transform(z, function() {
      var L = r.apply(this, arguments), V = this.__zoom, C = W == null ? E(L) : typeof W == "function" ? W.apply(this, arguments) : W;
      return i(ml.translate(C[0], C[1]).scale(V.k).translate(
        typeof B == "function" ? -B.apply(this, arguments) : -B,
        typeof F == "function" ? -F.apply(this, arguments) : -F
      ), L, f);
    }, W, T);
  };
  function $(z, B) {
    return B = Math.max(c[0], Math.min(c[1], B)), B === z.k ? z : new fn(B, z.x, z.y);
  }
  function _(z, B, F) {
    var W = B[0] - F[0] * z.k, T = B[1] - F[1] * z.k;
    return W === z.x && T === z.y ? z : new fn(z.k, W, T);
  }
  function E(z) {
    return [(+z[0][0] + +z[1][0]) / 2, (+z[0][1] + +z[1][1]) / 2];
  }
  function D(z, B, F, W) {
    z.on("start.zoom", function() {
      j(this, arguments).event(W).start();
    }).on("interrupt.zoom end.zoom", function() {
      j(this, arguments).event(W).end();
    }).tween("zoom", function() {
      var T = this, L = arguments, V = j(T, L).event(W), C = r.apply(T, L), A = F == null ? E(C) : typeof F == "function" ? F.apply(T, L) : F, te = Math.max(C[1][0] - C[0][0], C[1][1] - C[0][1]), re = T.__zoom, le = typeof B == "function" ? B.apply(T, L) : B, ue = p(re.invert(A).concat(te / re.k), le.invert(A).concat(te / le.k));
      return function(ae) {
        if (ae === 1) ae = le;
        else {
          var J = ue(ae), ce = te / J[2];
          ae = new fn(ce, A[0] - J[0] * ce, A[1] - J[1] * ce);
        }
        V.zoom(null, ae);
      };
    });
  }
  function j(z, B, F) {
    return !F && z.__zooming || new H(z, B);
  }
  function H(z, B) {
    this.that = z, this.args = B, this.active = 0, this.sourceEvent = null, this.extent = r.apply(z, B), this.taps = 0;
  }
  H.prototype = {
    event: function(z) {
      return z && (this.sourceEvent = z), this;
    },
    start: function() {
      return ++this.active === 1 && (this.that.__zooming = this, this.emit("start")), this;
    },
    zoom: function(z, B) {
      return this.mouse && z !== "mouse" && (this.mouse[1] = B.invert(this.mouse[0])), this.touch0 && z !== "touch" && (this.touch0[1] = B.invert(this.touch0[0])), this.touch1 && z !== "touch" && (this.touch1[1] = B.invert(this.touch1[0])), this.that.__zoom = B, this.emit("zoom"), this;
    },
    end: function() {
      return --this.active === 0 && (delete this.that.__zooming, this.emit("end")), this;
    },
    emit: function(z) {
      var B = wt(this.that).datum();
      m.call(
        z,
        this.that,
        new nx(z, {
          sourceEvent: this.sourceEvent,
          target: k,
          transform: this.that.__zoom,
          dispatch: m
        }),
        B
      );
    }
  };
  function b(z, ...B) {
    if (!t.apply(this, arguments)) return;
    var F = j(this, B).event(z), W = this.__zoom, T = Math.max(c[0], Math.min(c[1], W.k * Math.pow(2, l.apply(this, arguments)))), L = Dt(z);
    if (F.wheel)
      (F.mouse[0][0] !== L[0] || F.mouse[0][1] !== L[1]) && (F.mouse[1] = W.invert(F.mouse[0] = L)), clearTimeout(F.wheel);
    else {
      if (W.k === T) return;
      F.mouse = [L, W.invert(L)], qs(this), F.start();
    }
    Go(z), F.wheel = setTimeout(V, S), F.zoom("mouse", i(_($(W, T), F.mouse[0], F.mouse[1]), F.extent, f));
    function V() {
      F.wheel = null, F.end();
    }
  }
  function K(z, ...B) {
    if (y || !t.apply(this, arguments)) return;
    var F = z.currentTarget, W = j(this, B, !0).event(z), T = wt(z.view).on("mousemove.zoom", A, !0).on("mouseup.zoom", te, !0), L = Dt(z, F), V = z.clientX, C = z.clientY;
    hp(z.view), ka(z), W.mouse = [L, this.__zoom.invert(L)], qs(this), W.start();
    function A(re) {
      if (Go(re), !W.moved) {
        var le = re.clientX - V, ue = re.clientY - C;
        W.moved = le * le + ue * ue > M;
      }
      W.event(re).zoom("mouse", i(_(W.that.__zoom, W.mouse[0] = Dt(re, F), W.mouse[1]), W.extent, f));
    }
    function te(re) {
      T.on("mousemove.zoom mouseup.zoom", null), pp(re.view, W.moved), Go(re), W.event(re).end();
    }
  }
  function ee(z, ...B) {
    if (t.apply(this, arguments)) {
      var F = this.__zoom, W = Dt(z.changedTouches ? z.changedTouches[0] : z, this), T = F.invert(W), L = F.k * (z.shiftKey ? 0.5 : 2), V = i(_($(F, L), W, T), r.apply(this, B), f);
      Go(z), h > 0 ? wt(this).transition().duration(h).call(D, V, W, z) : wt(this).call(k.transform, V, W, z);
    }
  }
  function U(z, ...B) {
    if (t.apply(this, arguments)) {
      var F = z.touches, W = F.length, T = j(this, B, z.changedTouches.length === W).event(z), L, V, C, A;
      for (ka(z), V = 0; V < W; ++V)
        C = F[V], A = Dt(C, this), A = [A, this.__zoom.invert(A), C.identifier], T.touch0 ? !T.touch1 && T.touch0[2] !== A[2] && (T.touch1 = A, T.taps = 0) : (T.touch0 = A, L = !0, T.taps = 1 + !!v);
      v && (v = clearTimeout(v)), L && (T.taps < 2 && (g = A[0], v = setTimeout(function() {
        v = null;
      }, x)), qs(this), T.start());
    }
  }
  function G(z, ...B) {
    if (this.__zooming) {
      var F = j(this, B).event(z), W = z.changedTouches, T = W.length, L, V, C, A;
      for (Go(z), L = 0; L < T; ++L)
        V = W[L], C = Dt(V, this), F.touch0 && F.touch0[2] === V.identifier ? F.touch0[0] = C : F.touch1 && F.touch1[2] === V.identifier && (F.touch1[0] = C);
      if (V = F.that.__zoom, F.touch1) {
        var te = F.touch0[0], re = F.touch0[1], le = F.touch1[0], ue = F.touch1[1], ae = (ae = le[0] - te[0]) * ae + (ae = le[1] - te[1]) * ae, J = (J = ue[0] - re[0]) * J + (J = ue[1] - re[1]) * J;
        V = $(V, Math.sqrt(ae / J)), C = [(te[0] + le[0]) / 2, (te[1] + le[1]) / 2], A = [(re[0] + ue[0]) / 2, (re[1] + ue[1]) / 2];
      } else if (F.touch0) C = F.touch0[0], A = F.touch0[1];
      else return;
      F.zoom("touch", i(_(V, C, A), F.extent, f));
    }
  }
  function Q(z, ...B) {
    if (this.__zooming) {
      var F = j(this, B).event(z), W = z.changedTouches, T = W.length, L, V;
      for (ka(z), y && clearTimeout(y), y = setTimeout(function() {
        y = null;
      }, x), L = 0; L < T; ++L)
        V = W[L], F.touch0 && F.touch0[2] === V.identifier ? delete F.touch0 : F.touch1 && F.touch1[2] === V.identifier && delete F.touch1;
      if (F.touch1 && !F.touch0 && (F.touch0 = F.touch1, delete F.touch1), F.touch0) F.touch0[1] = this.__zoom.invert(F.touch0[0]);
      else if (F.end(), F.taps === 2 && (V = Dt(V, this), Math.hypot(g[0] - V[0], g[1] - V[1]) < N)) {
        var C = wt(this).on("dblclick.zoom");
        C && C.apply(this, arguments);
      }
    }
  }
  return k.wheelDelta = function(z) {
    return arguments.length ? (l = typeof z == "function" ? z : Bs(+z), k) : l;
  }, k.filter = function(z) {
    return arguments.length ? (t = typeof z == "function" ? z : Bs(!!z), k) : t;
  }, k.touchable = function(z) {
    return arguments.length ? (u = typeof z == "function" ? z : Bs(!!z), k) : u;
  }, k.extent = function(z) {
    return arguments.length ? (r = typeof z == "function" ? z : Bs([[+z[0][0], +z[0][1]], [+z[1][0], +z[1][1]]]), k) : r;
  }, k.scaleExtent = function(z) {
    return arguments.length ? (c[0] = +z[0], c[1] = +z[1], k) : [c[0], c[1]];
  }, k.translateExtent = function(z) {
    return arguments.length ? (f[0][0] = +z[0][0], f[1][0] = +z[1][0], f[0][1] = +z[0][1], f[1][1] = +z[1][1], k) : [[f[0][0], f[0][1]], [f[1][0], f[1][1]]];
  }, k.constrain = function(z) {
    return arguments.length ? (i = z, k) : i;
  }, k.duration = function(z) {
    return arguments.length ? (h = +z, k) : h;
  }, k.interpolate = function(z) {
    return arguments.length ? (p = z, k) : p;
  }, k.on = function() {
    var z = m.on.apply(m, arguments);
    return z === m ? k : z;
  }, k.clickDistance = function(z) {
    return arguments.length ? (M = (z = +z) * z, k) : Math.sqrt(M);
  }, k.tapDistance = function(z) {
    return arguments.length ? (N = +z, k) : N;
  }, k;
}
const Kt = {
  error001: () => "[React Flow]: Seems like you have not used zustand provider as an ancestor. Help: https://reactflow.dev/error#001",
  error002: () => "It looks like you've created a new nodeTypes or edgeTypes object. If this wasn't on purpose please define the nodeTypes/edgeTypes outside of the component or memoize them.",
  error003: (t) => `Node type "${t}" not found. Using fallback type "default".`,
  error004: () => "The React Flow parent container needs a width and a height to render the graph.",
  error005: () => "Only child nodes can use a parent extent.",
  error006: () => "Can't create edge. An edge needs a source and a target.",
  error007: (t) => `The old edge with id=${t} does not exist.`,
  error009: (t) => `Marker type "${t}" doesn't exist.`,
  error008: (t, { id: r, sourceHandle: i, targetHandle: l }) => `Couldn't create edge for ${t} handle id: "${t === "source" ? i : l}", edge id: ${r}.`,
  error010: () => "Handle: No node id found. Make sure to only use a Handle inside a custom Node.",
  error011: (t) => `Edge type "${t}" not found. Using fallback type "default".`,
  error012: (t) => `Node with id "${t}" does not exist, it may have been removed. This can happen when a node is deleted before the "onNodeClick" handler is called.`,
  error013: (t = "react") => `It seems that you haven't loaded the styles. Please import '@xyflow/${t}/dist/style.css' or base.css to make sure everything is working properly.`,
  error014: () => "useNodeConnections: No node ID found. Call useNodeConnections inside a custom Node or provide a node ID.",
  error015: () => "It seems that you are trying to drag a node that is not initialized. Please use onNodesChange as explained in the docs."
}, ii = [
  [Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY],
  [Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY]
], Tp = ["Enter", " ", "Escape"], Lp = {
  "node.a11yDescription.default": "Press enter or space to select a node. Press delete to remove it and escape to cancel.",
  "node.a11yDescription.keyboardDisabled": "Press enter or space to select a node. You can then use the arrow keys to move the node around. Press delete to remove it and escape to cancel.",
  "node.a11yDescription.ariaLiveMessage": ({ direction: t, x: r, y: i }) => `Moved selected node ${t}. New position, x: ${r}, y: ${i}`,
  "edge.a11yDescription.default": "Press enter or space to select an edge. You can then press delete to remove it or escape to cancel.",
  // Control elements
  "controls.ariaLabel": "Control Panel",
  "controls.zoomIn.ariaLabel": "Zoom In",
  "controls.zoomOut.ariaLabel": "Zoom Out",
  "controls.fitView.ariaLabel": "Fit View",
  "controls.interactive.ariaLabel": "Toggle Interactivity",
  // Mini map
  "minimap.ariaLabel": "Mini Map",
  // Handle
  "handle.ariaLabel": "Handle"
};
var Zr;
(function(t) {
  t.Strict = "strict", t.Loose = "loose";
})(Zr || (Zr = {}));
var ar;
(function(t) {
  t.Free = "free", t.Vertical = "vertical", t.Horizontal = "horizontal";
})(ar || (ar = {}));
var si;
(function(t) {
  t.Partial = "partial", t.Full = "full";
})(si || (si = {}));
const Ap = {
  inProgress: !1,
  isValid: null,
  from: null,
  fromHandle: null,
  fromPosition: null,
  fromNode: null,
  to: null,
  toHandle: null,
  toPosition: null,
  toNode: null,
  pointer: null
};
var On;
(function(t) {
  t.Bezier = "default", t.Straight = "straight", t.Step = "step", t.SmoothStep = "smoothstep", t.SimpleBezier = "simplebezier";
})(On || (On = {}));
var li;
(function(t) {
  t.Arrow = "arrow", t.ArrowClosed = "arrowclosed";
})(li || (li = {}));
var me;
(function(t) {
  t.Left = "left", t.Top = "top", t.Right = "right", t.Bottom = "bottom";
})(me || (me = {}));
const uh = {
  [me.Left]: me.Right,
  [me.Right]: me.Left,
  [me.Top]: me.Bottom,
  [me.Bottom]: me.Top
};
function Rp(t) {
  return t === null ? null : t ? "valid" : "invalid";
}
const $p = (t) => "id" in t && "source" in t && "target" in t, ux = (t) => "id" in t && "position" in t && !("source" in t) && !("target" in t), nc = (t) => "id" in t && "internals" in t && !("source" in t) && !("target" in t), pi = (t, r = [0, 0]) => {
  const { width: i, height: l } = hn(t), u = t.origin ?? r, c = i * u[0], f = l * u[1];
  return {
    x: t.position.x - c,
    y: t.position.y - f
  };
}, ax = (t, r = { nodeOrigin: [0, 0] }) => {
  if (t.length === 0)
    return { x: 0, y: 0, width: 0, height: 0 };
  const i = t.reduce((l, u) => {
    const c = typeof u == "string";
    let f = !r.nodeLookup && !c ? u : void 0;
    r.nodeLookup && (f = c ? r.nodeLookup.get(u) : nc(u) ? u : r.nodeLookup.get(u.id));
    const h = f ? sl(f, r.nodeOrigin) : { x: 0, y: 0, x2: 0, y2: 0 };
    return yl(l, h);
  }, { x: 1 / 0, y: 1 / 0, x2: -1 / 0, y2: -1 / 0 });
  return vl(i);
}, gi = (t, r = {}) => {
  let i = { x: 1 / 0, y: 1 / 0, x2: -1 / 0, y2: -1 / 0 }, l = !1;
  return t.forEach((u) => {
    (r.filter === void 0 || r.filter(u)) && (i = yl(i, sl(u)), l = !0);
  }), l ? vl(i) : { x: 0, y: 0, width: 0, height: 0 };
}, rc = (t, r, [i, l, u] = [0, 0, 1], c = !1, f = !1) => {
  const h = {
    ...yi(r, [i, l, u]),
    width: r.width / u,
    height: r.height / u
  }, p = [];
  for (const m of t.values()) {
    const { measured: v, selectable: g = !0, hidden: y = !1 } = m;
    if (f && !g || y)
      continue;
    const x = v.width ?? m.width ?? m.initialWidth ?? null, S = v.height ?? m.height ?? m.initialHeight ?? null, M = ui(h, Jr(m)), N = (x ?? 0) * (S ?? 0), k = c && M > 0;
    (!m.internals.handleBounds || k || M >= N || m.dragging) && p.push(m);
  }
  return p;
}, cx = (t, r) => {
  const i = /* @__PURE__ */ new Set();
  return t.forEach((l) => {
    i.add(l.id);
  }), r.filter((l) => i.has(l.source) || i.has(l.target));
};
function fx(t, r) {
  const i = /* @__PURE__ */ new Map(), l = r != null && r.nodes ? new Set(r.nodes.map((u) => u.id)) : null;
  return t.forEach((u) => {
    u.measured.width && u.measured.height && ((r == null ? void 0 : r.includeHiddenNodes) || !u.hidden) && (!l || l.has(u.id)) && i.set(u.id, u);
  }), i;
}
async function dx({ nodes: t, width: r, height: i, panZoom: l, minZoom: u, maxZoom: c }, f) {
  if (t.size === 0)
    return Promise.resolve(!0);
  const h = fx(t, f), p = gi(h), m = oc(p, r, i, (f == null ? void 0 : f.minZoom) ?? u, (f == null ? void 0 : f.maxZoom) ?? c, (f == null ? void 0 : f.padding) ?? 0.1);
  return await l.setViewport(m, {
    duration: f == null ? void 0 : f.duration,
    ease: f == null ? void 0 : f.ease,
    interpolate: f == null ? void 0 : f.interpolate
  }), Promise.resolve(!0);
}
function Dp({ nodeId: t, nextPosition: r, nodeLookup: i, nodeOrigin: l = [0, 0], nodeExtent: u, onError: c }) {
  const f = i.get(t), h = f.parentId ? i.get(f.parentId) : void 0, { x: p, y: m } = h ? h.internals.positionAbsolute : { x: 0, y: 0 }, v = f.origin ?? l;
  let g = f.extent || u;
  if (f.extent === "parent" && !f.expandParent)
    if (!h)
      c == null || c("005", Kt.error005());
    else {
      const x = h.measured.width, S = h.measured.height;
      x && S && (g = [
        [p, m],
        [p + x, m + S]
      ]);
    }
  else h && eo(f.extent) && (g = [
    [f.extent[0][0] + p, f.extent[0][1] + m],
    [f.extent[1][0] + p, f.extent[1][1] + m]
  ]);
  const y = eo(g) ? dr(r, g, f.measured) : r;
  return (f.measured.width === void 0 || f.measured.height === void 0) && (c == null || c("015", Kt.error015())), {
    position: {
      x: y.x - p + (f.measured.width ?? 0) * v[0],
      y: y.y - m + (f.measured.height ?? 0) * v[1]
    },
    positionAbsolute: y
  };
}
async function hx({ nodesToRemove: t = [], edgesToRemove: r = [], nodes: i, edges: l, onBeforeDelete: u }) {
  const c = new Set(t.map((y) => y.id)), f = [];
  for (const y of i) {
    if (y.deletable === !1)
      continue;
    const x = c.has(y.id), S = !x && y.parentId && f.find((M) => M.id === y.parentId);
    (x || S) && f.push(y);
  }
  const h = new Set(r.map((y) => y.id)), p = l.filter((y) => y.deletable !== !1), v = cx(f, p);
  for (const y of p)
    h.has(y.id) && !v.find((S) => S.id === y.id) && v.push(y);
  if (!u)
    return {
      edges: v,
      nodes: f
    };
  const g = await u({
    nodes: f,
    edges: v
  });
  return typeof g == "boolean" ? g ? { edges: v, nodes: f } : { edges: [], nodes: [] } : g;
}
const qr = (t, r = 0, i = 1) => Math.min(Math.max(t, r), i), dr = (t = { x: 0, y: 0 }, r, i) => ({
  x: qr(t.x, r[0][0], r[1][0] - ((i == null ? void 0 : i.width) ?? 0)),
  y: qr(t.y, r[0][1], r[1][1] - ((i == null ? void 0 : i.height) ?? 0))
});
function jp(t, r, i) {
  const { width: l, height: u } = hn(i), { x: c, y: f } = i.internals.positionAbsolute;
  return dr(t, [
    [c, f],
    [c + l, f + u]
  ], r);
}
const ah = (t, r, i) => t < r ? qr(Math.abs(t - r), 1, r) / r : t > i ? -qr(Math.abs(t - i), 1, r) / r : 0, Op = (t, r, i = 15, l = 40) => {
  const u = ah(t.x, l, r.width - l) * i, c = ah(t.y, l, r.height - l) * i;
  return [u, c];
}, yl = (t, r) => ({
  x: Math.min(t.x, r.x),
  y: Math.min(t.y, r.y),
  x2: Math.max(t.x2, r.x2),
  y2: Math.max(t.y2, r.y2)
}), Ba = ({ x: t, y: r, width: i, height: l }) => ({
  x: t,
  y: r,
  x2: t + i,
  y2: r + l
}), vl = ({ x: t, y: r, x2: i, y2: l }) => ({
  x: t,
  y: r,
  width: i - t,
  height: l - r
}), Jr = (t, r = [0, 0]) => {
  var u, c;
  const { x: i, y: l } = nc(t) ? t.internals.positionAbsolute : pi(t, r);
  return {
    x: i,
    y: l,
    width: ((u = t.measured) == null ? void 0 : u.width) ?? t.width ?? t.initialWidth ?? 0,
    height: ((c = t.measured) == null ? void 0 : c.height) ?? t.height ?? t.initialHeight ?? 0
  };
}, sl = (t, r = [0, 0]) => {
  var u, c;
  const { x: i, y: l } = nc(t) ? t.internals.positionAbsolute : pi(t, r);
  return {
    x: i,
    y: l,
    x2: i + (((u = t.measured) == null ? void 0 : u.width) ?? t.width ?? t.initialWidth ?? 0),
    y2: l + (((c = t.measured) == null ? void 0 : c.height) ?? t.height ?? t.initialHeight ?? 0)
  };
}, Fp = (t, r) => vl(yl(Ba(t), Ba(r))), ui = (t, r) => {
  const i = Math.max(0, Math.min(t.x + t.width, r.x + r.width) - Math.max(t.x, r.x)), l = Math.max(0, Math.min(t.y + t.height, r.y + r.height) - Math.max(t.y, r.y));
  return Math.ceil(i * l);
}, ch = (t) => Ot(t.width) && Ot(t.height) && Ot(t.x) && Ot(t.y), Ot = (t) => !isNaN(t) && isFinite(t), px = (t, r) => {
}, mi = (t, r = [1, 1]) => ({
  x: r[0] * Math.round(t.x / r[0]),
  y: r[1] * Math.round(t.y / r[1])
}), yi = ({ x: t, y: r }, [i, l, u], c = !1, f = [1, 1]) => {
  const h = {
    x: (t - i) / u,
    y: (r - l) / u
  };
  return c ? mi(h, f) : h;
}, ll = ({ x: t, y: r }, [i, l, u]) => ({
  x: t * u + i,
  y: r * u + l
});
function Ur(t, r) {
  if (typeof t == "number")
    return Math.floor((r - r / (1 + t)) * 0.5);
  if (typeof t == "string" && t.endsWith("px")) {
    const i = parseFloat(t);
    if (!Number.isNaN(i))
      return Math.floor(i);
  }
  if (typeof t == "string" && t.endsWith("%")) {
    const i = parseFloat(t);
    if (!Number.isNaN(i))
      return Math.floor(r * i * 0.01);
  }
  return console.error(`[React Flow] The padding value "${t}" is invalid. Please provide a number or a string with a valid unit (px or %).`), 0;
}
function gx(t, r, i) {
  if (typeof t == "string" || typeof t == "number") {
    const l = Ur(t, i), u = Ur(t, r);
    return {
      top: l,
      right: u,
      bottom: l,
      left: u,
      x: u * 2,
      y: l * 2
    };
  }
  if (typeof t == "object") {
    const l = Ur(t.top ?? t.y ?? 0, i), u = Ur(t.bottom ?? t.y ?? 0, i), c = Ur(t.left ?? t.x ?? 0, r), f = Ur(t.right ?? t.x ?? 0, r);
    return { top: l, right: f, bottom: u, left: c, x: c + f, y: l + u };
  }
  return { top: 0, right: 0, bottom: 0, left: 0, x: 0, y: 0 };
}
function mx(t, r, i, l, u, c) {
  const { x: f, y: h } = ll(t, [r, i, l]), { x: p, y: m } = ll({ x: t.x + t.width, y: t.y + t.height }, [r, i, l]), v = u - p, g = c - m;
  return {
    left: Math.floor(f),
    top: Math.floor(h),
    right: Math.floor(v),
    bottom: Math.floor(g)
  };
}
const oc = (t, r, i, l, u, c) => {
  const f = gx(c, r, i), h = (r - f.x) / t.width, p = (i - f.y) / t.height, m = Math.min(h, p), v = qr(m, l, u), g = t.x + t.width / 2, y = t.y + t.height / 2, x = r / 2 - g * v, S = i / 2 - y * v, M = mx(t, x, S, v, r, i), N = {
    left: Math.min(M.left - f.left, 0),
    top: Math.min(M.top - f.top, 0),
    right: Math.min(M.right - f.right, 0),
    bottom: Math.min(M.bottom - f.bottom, 0)
  };
  return {
    x: x - N.left + N.right,
    y: S - N.top + N.bottom,
    zoom: v
  };
}, ai = () => {
  var t;
  return typeof navigator < "u" && ((t = navigator == null ? void 0 : navigator.userAgent) == null ? void 0 : t.indexOf("Mac")) >= 0;
};
function eo(t) {
  return t != null && t !== "parent";
}
function hn(t) {
  var r, i;
  return {
    width: ((r = t.measured) == null ? void 0 : r.width) ?? t.width ?? t.initialWidth ?? 0,
    height: ((i = t.measured) == null ? void 0 : i.height) ?? t.height ?? t.initialHeight ?? 0
  };
}
function Hp(t) {
  var r, i;
  return (((r = t.measured) == null ? void 0 : r.width) ?? t.width ?? t.initialWidth) !== void 0 && (((i = t.measured) == null ? void 0 : i.height) ?? t.height ?? t.initialHeight) !== void 0;
}
function Vp(t, r = { width: 0, height: 0 }, i, l, u) {
  const c = { ...t }, f = l.get(i);
  if (f) {
    const h = f.origin || u;
    c.x += f.internals.positionAbsolute.x - (r.width ?? 0) * h[0], c.y += f.internals.positionAbsolute.y - (r.height ?? 0) * h[1];
  }
  return c;
}
function fh(t, r) {
  if (t.size !== r.size)
    return !1;
  for (const i of t)
    if (!r.has(i))
      return !1;
  return !0;
}
function yx() {
  let t, r;
  return { promise: new Promise((l, u) => {
    t = l, r = u;
  }), resolve: t, reject: r };
}
function vx(t) {
  return { ...Lp, ...t || {} };
}
function ei(t, { snapGrid: r = [0, 0], snapToGrid: i = !1, transform: l, containerBounds: u }) {
  const { x: c, y: f } = Ft(t), h = yi({ x: c - ((u == null ? void 0 : u.left) ?? 0), y: f - ((u == null ? void 0 : u.top) ?? 0) }, l), { x: p, y: m } = i ? mi(h, r) : h;
  return {
    xSnapped: p,
    ySnapped: m,
    ...h
  };
}
const ic = (t) => ({
  width: t.offsetWidth,
  height: t.offsetHeight
}), Bp = (t) => {
  var r;
  return ((r = t == null ? void 0 : t.getRootNode) == null ? void 0 : r.call(t)) || (window == null ? void 0 : window.document);
}, wx = ["INPUT", "SELECT", "TEXTAREA"];
function Wp(t) {
  var l, u;
  const r = ((u = (l = t.composedPath) == null ? void 0 : l.call(t)) == null ? void 0 : u[0]) || t.target;
  return (r == null ? void 0 : r.nodeType) !== 1 ? !1 : wx.includes(r.nodeName) || r.hasAttribute("contenteditable") || !!r.closest(".nokey");
}
const Up = (t) => "clientX" in t, Ft = (t, r) => {
  var c, f;
  const i = Up(t), l = i ? t.clientX : (c = t.touches) == null ? void 0 : c[0].clientX, u = i ? t.clientY : (f = t.touches) == null ? void 0 : f[0].clientY;
  return {
    x: l - ((r == null ? void 0 : r.left) ?? 0),
    y: u - ((r == null ? void 0 : r.top) ?? 0)
  };
}, dh = (t, r, i, l, u) => {
  const c = r.querySelectorAll(`.${t}`);
  return !c || !c.length ? null : Array.from(c).map((f) => {
    const h = f.getBoundingClientRect();
    return {
      id: f.getAttribute("data-handleid"),
      type: t,
      nodeId: u,
      position: f.getAttribute("data-handlepos"),
      x: (h.left - i.left) / l,
      y: (h.top - i.top) / l,
      ...ic(f)
    };
  });
};
function Yp({ sourceX: t, sourceY: r, targetX: i, targetY: l, sourceControlX: u, sourceControlY: c, targetControlX: f, targetControlY: h }) {
  const p = t * 0.125 + u * 0.375 + f * 0.375 + i * 0.125, m = r * 0.125 + c * 0.375 + h * 0.375 + l * 0.125, v = Math.abs(p - t), g = Math.abs(m - r);
  return [p, m, v, g];
}
function Ws(t, r) {
  return t >= 0 ? 0.5 * t : r * 25 * Math.sqrt(-t);
}
function hh({ pos: t, x1: r, y1: i, x2: l, y2: u, c }) {
  switch (t) {
    case me.Left:
      return [r - Ws(r - l, c), i];
    case me.Right:
      return [r + Ws(l - r, c), i];
    case me.Top:
      return [r, i - Ws(i - u, c)];
    case me.Bottom:
      return [r, i + Ws(u - i, c)];
  }
}
function bp({ sourceX: t, sourceY: r, sourcePosition: i = me.Bottom, targetX: l, targetY: u, targetPosition: c = me.Top, curvature: f = 0.25 }) {
  const [h, p] = hh({
    pos: i,
    x1: t,
    y1: r,
    x2: l,
    y2: u,
    c: f
  }), [m, v] = hh({
    pos: c,
    x1: l,
    y1: u,
    x2: t,
    y2: r,
    c: f
  }), [g, y, x, S] = Yp({
    sourceX: t,
    sourceY: r,
    targetX: l,
    targetY: u,
    sourceControlX: h,
    sourceControlY: p,
    targetControlX: m,
    targetControlY: v
  });
  return [
    `M${t},${r} C${h},${p} ${m},${v} ${l},${u}`,
    g,
    y,
    x,
    S
  ];
}
function Xp({ sourceX: t, sourceY: r, targetX: i, targetY: l }) {
  const u = Math.abs(i - t) / 2, c = i < t ? i + u : i - u, f = Math.abs(l - r) / 2, h = l < r ? l + f : l - f;
  return [c, h, u, f];
}
function xx({ sourceNode: t, targetNode: r, selected: i = !1, zIndex: l = 0, elevateOnSelect: u = !1, zIndexMode: c = "basic" }) {
  if (c === "manual")
    return l;
  const f = u && i ? l + 1e3 : l, h = Math.max(t.parentId || u && t.selected ? t.internals.z : 0, r.parentId || u && r.selected ? r.internals.z : 0);
  return f + h;
}
function Sx({ sourceNode: t, targetNode: r, width: i, height: l, transform: u }) {
  const c = yl(sl(t), sl(r));
  c.x === c.x2 && (c.x2 += 1), c.y === c.y2 && (c.y2 += 1);
  const f = {
    x: -u[0] / u[2],
    y: -u[1] / u[2],
    width: i / u[2],
    height: l / u[2]
  };
  return ui(f, vl(c)) > 0;
}
const _x = ({ source: t, sourceHandle: r, target: i, targetHandle: l }) => `xy-edge__${t}${r || ""}-${i}${l || ""}`, Ex = (t, r) => r.some((i) => i.source === t.source && i.target === t.target && (i.sourceHandle === t.sourceHandle || !i.sourceHandle && !t.sourceHandle) && (i.targetHandle === t.targetHandle || !i.targetHandle && !t.targetHandle)), kx = (t, r, i = {}) => {
  if (!t.source || !t.target)
    return r;
  const l = i.getEdgeId || _x;
  let u;
  return $p(t) ? u = { ...t } : u = {
    ...t,
    id: l(t)
  }, Ex(u, r) ? r : (u.sourceHandle === null && delete u.sourceHandle, u.targetHandle === null && delete u.targetHandle, r.concat(u));
};
function Qp({ sourceX: t, sourceY: r, targetX: i, targetY: l }) {
  const [u, c, f, h] = Xp({
    sourceX: t,
    sourceY: r,
    targetX: i,
    targetY: l
  });
  return [`M ${t},${r}L ${i},${l}`, u, c, f, h];
}
const ph = {
  [me.Left]: { x: -1, y: 0 },
  [me.Right]: { x: 1, y: 0 },
  [me.Top]: { x: 0, y: -1 },
  [me.Bottom]: { x: 0, y: 1 }
}, Nx = ({ source: t, sourcePosition: r = me.Bottom, target: i }) => r === me.Left || r === me.Right ? t.x < i.x ? { x: 1, y: 0 } : { x: -1, y: 0 } : t.y < i.y ? { x: 0, y: 1 } : { x: 0, y: -1 }, gh = (t, r) => Math.sqrt(Math.pow(r.x - t.x, 2) + Math.pow(r.y - t.y, 2));
function Cx({ source: t, sourcePosition: r = me.Bottom, target: i, targetPosition: l = me.Top, center: u, offset: c, stepPosition: f }) {
  const h = ph[r], p = ph[l], m = { x: t.x + h.x * c, y: t.y + h.y * c }, v = { x: i.x + p.x * c, y: i.y + p.y * c }, g = Nx({
    source: m,
    sourcePosition: r,
    target: v
  }), y = g.x !== 0 ? "x" : "y", x = g[y];
  let S = [], M, N;
  const k = { x: 0, y: 0 }, $ = { x: 0, y: 0 }, [, , _, E] = Xp({
    sourceX: t.x,
    sourceY: t.y,
    targetX: i.x,
    targetY: i.y
  });
  if (h[y] * p[y] === -1) {
    y === "x" ? (M = u.x ?? m.x + (v.x - m.x) * f, N = u.y ?? (m.y + v.y) / 2) : (M = u.x ?? (m.x + v.x) / 2, N = u.y ?? m.y + (v.y - m.y) * f);
    const b = [
      { x: M, y: m.y },
      { x: M, y: v.y }
    ], K = [
      { x: m.x, y: N },
      { x: v.x, y: N }
    ];
    h[y] === x ? S = y === "x" ? b : K : S = y === "x" ? K : b;
  } else {
    const b = [{ x: m.x, y: v.y }], K = [{ x: v.x, y: m.y }];
    if (y === "x" ? S = h.x === x ? K : b : S = h.y === x ? b : K, r === l) {
      const z = Math.abs(t[y] - i[y]);
      if (z <= c) {
        const B = Math.min(c - 1, c - z);
        h[y] === x ? k[y] = (m[y] > t[y] ? -1 : 1) * B : $[y] = (v[y] > i[y] ? -1 : 1) * B;
      }
    }
    if (r !== l) {
      const z = y === "x" ? "y" : "x", B = h[y] === p[z], F = m[z] > v[z], W = m[z] < v[z];
      (h[y] === 1 && (!B && F || B && W) || h[y] !== 1 && (!B && W || B && F)) && (S = y === "x" ? b : K);
    }
    const ee = { x: m.x + k.x, y: m.y + k.y }, U = { x: v.x + $.x, y: v.y + $.y }, G = Math.max(Math.abs(ee.x - S[0].x), Math.abs(U.x - S[0].x)), Q = Math.max(Math.abs(ee.y - S[0].y), Math.abs(U.y - S[0].y));
    G >= Q ? (M = (ee.x + U.x) / 2, N = S[0].y) : (M = S[0].x, N = (ee.y + U.y) / 2);
  }
  const D = { x: m.x + k.x, y: m.y + k.y }, j = { x: v.x + $.x, y: v.y + $.y };
  return [[
    t,
    // we only want to add the gapped source/target if they are different from the first/last point to avoid duplicates which can cause issues with the bends
    ...D.x !== S[0].x || D.y !== S[0].y ? [D] : [],
    ...S,
    ...j.x !== S[S.length - 1].x || j.y !== S[S.length - 1].y ? [j] : [],
    i
  ], M, N, _, E];
}
function Mx(t, r, i, l) {
  const u = Math.min(gh(t, r) / 2, gh(r, i) / 2, l), { x: c, y: f } = r;
  if (t.x === c && c === i.x || t.y === f && f === i.y)
    return `L${c} ${f}`;
  if (t.y === f) {
    const m = t.x < i.x ? -1 : 1, v = t.y < i.y ? 1 : -1;
    return `L ${c + u * m},${f}Q ${c},${f} ${c},${f + u * v}`;
  }
  const h = t.x < i.x ? 1 : -1, p = t.y < i.y ? -1 : 1;
  return `L ${c},${f + u * p}Q ${c},${f} ${c + u * h},${f}`;
}
function ul({ sourceX: t, sourceY: r, sourcePosition: i = me.Bottom, targetX: l, targetY: u, targetPosition: c = me.Top, borderRadius: f = 5, centerX: h, centerY: p, offset: m = 20, stepPosition: v = 0.5 }) {
  const [g, y, x, S, M] = Cx({
    source: { x: t, y: r },
    sourcePosition: i,
    target: { x: l, y: u },
    targetPosition: c,
    center: { x: h, y: p },
    offset: m,
    stepPosition: v
  });
  let N = `M${g[0].x} ${g[0].y}`;
  for (let k = 1; k < g.length - 1; k++)
    N += Mx(g[k - 1], g[k], g[k + 1], f);
  return N += `L${g[g.length - 1].x} ${g[g.length - 1].y}`, [N, y, x, S, M];
}
function mh(t) {
  var r;
  return t && !!(t.internals.handleBounds || (r = t.handles) != null && r.length) && !!(t.measured.width || t.width || t.initialWidth);
}
function Px(t) {
  var g;
  const { sourceNode: r, targetNode: i } = t;
  if (!mh(r) || !mh(i))
    return null;
  const l = r.internals.handleBounds || yh(r.handles), u = i.internals.handleBounds || yh(i.handles), c = vh((l == null ? void 0 : l.source) ?? [], t.sourceHandle), f = vh(
    // when connection type is loose we can define all handles as sources and connect source -> source
    t.connectionMode === Zr.Strict ? (u == null ? void 0 : u.target) ?? [] : ((u == null ? void 0 : u.target) ?? []).concat((u == null ? void 0 : u.source) ?? []),
    t.targetHandle
  );
  if (!c || !f)
    return (g = t.onError) == null || g.call(t, "008", Kt.error008(c ? "target" : "source", {
      id: t.id,
      sourceHandle: t.sourceHandle,
      targetHandle: t.targetHandle
    })), null;
  const h = (c == null ? void 0 : c.position) || me.Bottom, p = (f == null ? void 0 : f.position) || me.Top, m = hr(r, c, h), v = hr(i, f, p);
  return {
    sourceX: m.x,
    sourceY: m.y,
    targetX: v.x,
    targetY: v.y,
    sourcePosition: h,
    targetPosition: p
  };
}
function yh(t) {
  if (!t)
    return null;
  const r = [], i = [];
  for (const l of t)
    l.width = l.width ?? 1, l.height = l.height ?? 1, l.type === "source" ? r.push(l) : l.type === "target" && i.push(l);
  return {
    source: r,
    target: i
  };
}
function hr(t, r, i = me.Left, l = !1) {
  const u = ((r == null ? void 0 : r.x) ?? 0) + t.internals.positionAbsolute.x, c = ((r == null ? void 0 : r.y) ?? 0) + t.internals.positionAbsolute.y, { width: f, height: h } = r ?? hn(t);
  if (l)
    return { x: u + f / 2, y: c + h / 2 };
  switch ((r == null ? void 0 : r.position) ?? i) {
    case me.Top:
      return { x: u + f / 2, y: c };
    case me.Right:
      return { x: u + f, y: c + h / 2 };
    case me.Bottom:
      return { x: u + f / 2, y: c + h };
    case me.Left:
      return { x: u, y: c + h / 2 };
  }
}
function vh(t, r) {
  return t && (r ? t.find((i) => i.id === r) : t[0]) || null;
}
function Wa(t, r) {
  return t ? typeof t == "string" ? t : `${r ? `${r}__` : ""}${Object.keys(t).sort().map((l) => `${l}=${t[l]}`).join("&")}` : "";
}
function Ix(t, { id: r, defaultColor: i, defaultMarkerStart: l, defaultMarkerEnd: u }) {
  const c = /* @__PURE__ */ new Set();
  return t.reduce((f, h) => ([h.markerStart || l, h.markerEnd || u].forEach((p) => {
    if (p && typeof p == "object") {
      const m = Wa(p, r);
      c.has(m) || (f.push({ id: m, color: p.color || i, ...p }), c.add(m));
    }
  }), f), []).sort((f, h) => f.id.localeCompare(h.id));
}
const Kp = 1e3, zx = 10, sc = {
  nodeOrigin: [0, 0],
  nodeExtent: ii,
  elevateNodesOnSelect: !0,
  zIndexMode: "basic",
  defaults: {}
}, Tx = {
  ...sc,
  checkEquality: !0
};
function lc(t, r) {
  const i = { ...t };
  for (const l in r)
    r[l] !== void 0 && (i[l] = r[l]);
  return i;
}
function Lx(t, r, i) {
  const l = lc(sc, i);
  for (const u of t.values())
    if (u.parentId)
      ac(u, t, r, l);
    else {
      const c = pi(u, l.nodeOrigin), f = eo(u.extent) ? u.extent : l.nodeExtent, h = dr(c, f, hn(u));
      u.internals.positionAbsolute = h;
    }
}
function Ax(t, r) {
  if (!t.handles)
    return t.measured ? r == null ? void 0 : r.internals.handleBounds : void 0;
  const i = [], l = [];
  for (const u of t.handles) {
    const c = {
      id: u.id,
      width: u.width ?? 1,
      height: u.height ?? 1,
      nodeId: t.id,
      x: u.x,
      y: u.y,
      position: u.position,
      type: u.type
    };
    u.type === "source" ? i.push(c) : u.type === "target" && l.push(c);
  }
  return {
    source: i,
    target: l
  };
}
function uc(t) {
  return t === "manual";
}
function Ua(t, r, i, l = {}) {
  var v, g;
  const u = lc(Tx, l), c = { i: 0 }, f = new Map(r), h = u != null && u.elevateNodesOnSelect && !uc(u.zIndexMode) ? Kp : 0;
  let p = t.length > 0, m = !1;
  r.clear(), i.clear();
  for (const y of t) {
    let x = f.get(y.id);
    if (u.checkEquality && y === (x == null ? void 0 : x.internals.userNode))
      r.set(y.id, x);
    else {
      const S = pi(y, u.nodeOrigin), M = eo(y.extent) ? y.extent : u.nodeExtent, N = dr(S, M, hn(y));
      x = {
        ...u.defaults,
        ...y,
        measured: {
          width: (v = y.measured) == null ? void 0 : v.width,
          height: (g = y.measured) == null ? void 0 : g.height
        },
        internals: {
          positionAbsolute: N,
          // if user re-initializes the node or removes `measured` for whatever reason, we reset the handleBounds so that the node gets re-measured
          handleBounds: Ax(y, x),
          z: Gp(y, h, u.zIndexMode),
          userNode: y
        }
      }, r.set(y.id, x);
    }
    (x.measured === void 0 || x.measured.width === void 0 || x.measured.height === void 0) && !x.hidden && (p = !1), y.parentId && ac(x, r, i, l, c), m || (m = y.selected ?? !1);
  }
  return { nodesInitialized: p, hasSelectedNodes: m };
}
function Rx(t, r) {
  if (!t.parentId)
    return;
  const i = r.get(t.parentId);
  i ? i.set(t.id, t) : r.set(t.parentId, /* @__PURE__ */ new Map([[t.id, t]]));
}
function ac(t, r, i, l, u) {
  const { elevateNodesOnSelect: c, nodeOrigin: f, nodeExtent: h, zIndexMode: p } = lc(sc, l), m = t.parentId, v = r.get(m);
  if (!v) {
    console.warn(`Parent node ${m} not found. Please make sure that parent nodes are in front of their child nodes in the nodes array.`);
    return;
  }
  Rx(t, i), u && !v.parentId && v.internals.rootParentIndex === void 0 && p === "auto" && (v.internals.rootParentIndex = ++u.i, v.internals.z = v.internals.z + u.i * zx), u && v.internals.rootParentIndex !== void 0 && (u.i = v.internals.rootParentIndex);
  const g = c && !uc(p) ? Kp : 0, { x: y, y: x, z: S } = $x(t, v, f, h, g, p), { positionAbsolute: M } = t.internals, N = y !== M.x || x !== M.y;
  (N || S !== t.internals.z) && r.set(t.id, {
    ...t,
    internals: {
      ...t.internals,
      positionAbsolute: N ? { x: y, y: x } : M,
      z: S
    }
  });
}
function Gp(t, r, i) {
  const l = Ot(t.zIndex) ? t.zIndex : 0;
  return uc(i) ? l : l + (t.selected ? r : 0);
}
function $x(t, r, i, l, u, c) {
  const { x: f, y: h } = r.internals.positionAbsolute, p = hn(t), m = pi(t, i), v = eo(t.extent) ? dr(m, t.extent, p) : m;
  let g = dr({ x: f + v.x, y: h + v.y }, l, p);
  t.extent === "parent" && (g = jp(g, p, r));
  const y = Gp(t, u, c), x = r.internals.z ?? 0;
  return {
    x: g.x,
    y: g.y,
    z: x >= y ? x + 1 : y
  };
}
function cc(t, r, i, l = [0, 0]) {
  var f;
  const u = [], c = /* @__PURE__ */ new Map();
  for (const h of t) {
    const p = r.get(h.parentId);
    if (!p)
      continue;
    const m = ((f = c.get(h.parentId)) == null ? void 0 : f.expandedRect) ?? Jr(p), v = Fp(m, h.rect);
    c.set(h.parentId, { expandedRect: v, parent: p });
  }
  return c.size > 0 && c.forEach(({ expandedRect: h, parent: p }, m) => {
    var _;
    const v = p.internals.positionAbsolute, g = hn(p), y = p.origin ?? l, x = h.x < v.x ? Math.round(Math.abs(v.x - h.x)) : 0, S = h.y < v.y ? Math.round(Math.abs(v.y - h.y)) : 0, M = Math.max(g.width, Math.round(h.width)), N = Math.max(g.height, Math.round(h.height)), k = (M - g.width) * y[0], $ = (N - g.height) * y[1];
    (x > 0 || S > 0 || k || $) && (u.push({
      id: m,
      type: "position",
      position: {
        x: p.position.x - x + k,
        y: p.position.y - S + $
      }
    }), (_ = i.get(m)) == null || _.forEach((E) => {
      t.some((D) => D.id === E.id) || u.push({
        id: E.id,
        type: "position",
        position: {
          x: E.position.x + x,
          y: E.position.y + S
        }
      });
    })), (g.width < h.width || g.height < h.height || x || S) && u.push({
      id: m,
      type: "dimensions",
      setAttributes: !0,
      dimensions: {
        width: M + (x ? y[0] * x - k : 0),
        height: N + (S ? y[1] * S - $ : 0)
      }
    });
  }), u;
}
function Dx(t, r, i, l, u, c, f) {
  const h = l == null ? void 0 : l.querySelector(".xyflow__viewport");
  let p = !1;
  if (!h)
    return { changes: [], updatedInternals: p };
  const m = [], v = window.getComputedStyle(h), { m22: g } = new window.DOMMatrixReadOnly(v.transform), y = [];
  for (const x of t.values()) {
    const S = r.get(x.id);
    if (!S)
      continue;
    if (S.hidden) {
      r.set(S.id, {
        ...S,
        internals: {
          ...S.internals,
          handleBounds: void 0
        }
      }), p = !0;
      continue;
    }
    const M = ic(x.nodeElement), N = S.measured.width !== M.width || S.measured.height !== M.height;
    if (!!(M.width && M.height && (N || !S.internals.handleBounds || x.force))) {
      const $ = x.nodeElement.getBoundingClientRect(), _ = eo(S.extent) ? S.extent : c;
      let { positionAbsolute: E } = S.internals;
      S.parentId && S.extent === "parent" ? E = jp(E, M, r.get(S.parentId)) : _ && (E = dr(E, _, M));
      const D = {
        ...S,
        measured: M,
        internals: {
          ...S.internals,
          positionAbsolute: E,
          handleBounds: {
            source: dh("source", x.nodeElement, $, g, S.id),
            target: dh("target", x.nodeElement, $, g, S.id)
          }
        }
      };
      r.set(S.id, D), S.parentId && ac(D, r, i, { nodeOrigin: u, zIndexMode: f }), p = !0, N && (m.push({
        id: S.id,
        type: "dimensions",
        dimensions: M
      }), S.expandParent && S.parentId && y.push({
        id: S.id,
        parentId: S.parentId,
        rect: Jr(D, u)
      }));
    }
  }
  if (y.length > 0) {
    const x = cc(y, r, i, u);
    m.push(...x);
  }
  return { changes: m, updatedInternals: p };
}
async function jx({ delta: t, panZoom: r, transform: i, translateExtent: l, width: u, height: c }) {
  if (!r || !t.x && !t.y)
    return Promise.resolve(!1);
  const f = await r.setViewportConstrained({
    x: i[0] + t.x,
    y: i[1] + t.y,
    zoom: i[2]
  }, [
    [0, 0],
    [u, c]
  ], l), h = !!f && (f.x !== i[0] || f.y !== i[1] || f.k !== i[2]);
  return Promise.resolve(h);
}
function wh(t, r, i, l, u, c) {
  let f = u;
  const h = l.get(f) || /* @__PURE__ */ new Map();
  l.set(f, h.set(i, r)), f = `${u}-${t}`;
  const p = l.get(f) || /* @__PURE__ */ new Map();
  if (l.set(f, p.set(i, r)), c) {
    f = `${u}-${t}-${c}`;
    const m = l.get(f) || /* @__PURE__ */ new Map();
    l.set(f, m.set(i, r));
  }
}
function Zp(t, r, i) {
  t.clear(), r.clear();
  for (const l of i) {
    const { source: u, target: c, sourceHandle: f = null, targetHandle: h = null } = l, p = { edgeId: l.id, source: u, target: c, sourceHandle: f, targetHandle: h }, m = `${u}-${f}--${c}-${h}`, v = `${c}-${h}--${u}-${f}`;
    wh("source", p, v, t, u, f), wh("target", p, m, t, c, h), r.set(l.id, l);
  }
}
function qp(t, r) {
  if (!t.parentId)
    return !1;
  const i = r.get(t.parentId);
  return i ? i.selected ? !0 : qp(i, r) : !1;
}
function xh(t, r, i) {
  var u;
  let l = t;
  do {
    if ((u = l == null ? void 0 : l.matches) != null && u.call(l, r))
      return !0;
    if (l === i)
      return !1;
    l = l == null ? void 0 : l.parentElement;
  } while (l);
  return !1;
}
function Ox(t, r, i, l) {
  const u = /* @__PURE__ */ new Map();
  for (const [c, f] of t)
    if ((f.selected || f.id === l) && (!f.parentId || !qp(f, t)) && (f.draggable || r && typeof f.draggable > "u")) {
      const h = t.get(c);
      h && u.set(c, {
        id: c,
        position: h.position || { x: 0, y: 0 },
        distance: {
          x: i.x - h.internals.positionAbsolute.x,
          y: i.y - h.internals.positionAbsolute.y
        },
        extent: h.extent,
        parentId: h.parentId,
        origin: h.origin,
        expandParent: h.expandParent,
        internals: {
          positionAbsolute: h.internals.positionAbsolute || { x: 0, y: 0 }
        },
        measured: {
          width: h.measured.width ?? 0,
          height: h.measured.height ?? 0
        }
      });
    }
  return u;
}
function Na({ nodeId: t, dragItems: r, nodeLookup: i, dragging: l = !0 }) {
  var f, h, p;
  const u = [];
  for (const [m, v] of r) {
    const g = (f = i.get(m)) == null ? void 0 : f.internals.userNode;
    g && u.push({
      ...g,
      position: v.position,
      dragging: l
    });
  }
  if (!t)
    return [u[0], u];
  const c = (h = i.get(t)) == null ? void 0 : h.internals.userNode;
  return [
    c ? {
      ...c,
      position: ((p = r.get(t)) == null ? void 0 : p.position) || c.position,
      dragging: l
    } : u[0],
    u
  ];
}
function Fx({ dragItems: t, snapGrid: r, x: i, y: l }) {
  const u = t.values().next().value;
  if (!u)
    return null;
  const c = {
    x: i - u.distance.x,
    y: l - u.distance.y
  }, f = mi(c, r);
  return {
    x: f.x - c.x,
    y: f.y - c.y
  };
}
function Hx({ onNodeMouseDown: t, getStoreItems: r, onDragStart: i, onDrag: l, onDragStop: u }) {
  let c = { x: null, y: null }, f = 0, h = /* @__PURE__ */ new Map(), p = !1, m = { x: 0, y: 0 }, v = null, g = !1, y = null, x = !1, S = !1, M = null;
  function N({ noDragClassName: $, handleSelector: _, domNode: E, isSelectable: D, nodeId: j, nodeClickDistance: H = 0 }) {
    y = wt(E);
    function b({ x: G, y: Q }) {
      const { nodeLookup: z, nodeExtent: B, snapGrid: F, snapToGrid: W, nodeOrigin: T, onNodeDrag: L, onSelectionDrag: V, onError: C, updateNodePositions: A } = r();
      c = { x: G, y: Q };
      let te = !1;
      const re = h.size > 1, le = re && B ? Ba(gi(h)) : null, ue = re && W ? Fx({
        dragItems: h,
        snapGrid: F,
        x: G,
        y: Q
      }) : null;
      for (const [ae, J] of h) {
        if (!z.has(ae))
          continue;
        let ce = { x: G - J.distance.x, y: Q - J.distance.y };
        W && (ce = ue ? {
          x: Math.round(ce.x + ue.x),
          y: Math.round(ce.y + ue.y)
        } : mi(ce, F));
        let xe = null;
        if (re && B && !J.extent && le) {
          const { positionAbsolute: ye } = J.internals, Ne = ye.x - le.x + B[0][0], Te = ye.x + J.measured.width - le.x2 + B[1][0], Ce = ye.y - le.y + B[0][1], We = ye.y + J.measured.height - le.y2 + B[1][1];
          xe = [
            [Ne, Ce],
            [Te, We]
          ];
        }
        const { position: _e, positionAbsolute: Se } = Dp({
          nodeId: ae,
          nextPosition: ce,
          nodeLookup: z,
          nodeExtent: xe || B,
          nodeOrigin: T,
          onError: C
        });
        te = te || J.position.x !== _e.x || J.position.y !== _e.y, J.position = _e, J.internals.positionAbsolute = Se;
      }
      if (S = S || te, !!te && (A(h, !0), M && (l || L || !j && V))) {
        const [ae, J] = Na({
          nodeId: j,
          dragItems: h,
          nodeLookup: z
        });
        l == null || l(M, h, ae, J), L == null || L(M, ae, J), j || V == null || V(M, J);
      }
    }
    async function K() {
      if (!v)
        return;
      const { transform: G, panBy: Q, autoPanSpeed: z, autoPanOnNodeDrag: B } = r();
      if (!B) {
        p = !1, cancelAnimationFrame(f);
        return;
      }
      const [F, W] = Op(m, v, z);
      (F !== 0 || W !== 0) && (c.x = (c.x ?? 0) - F / G[2], c.y = (c.y ?? 0) - W / G[2], await Q({ x: F, y: W }) && b(c)), f = requestAnimationFrame(K);
    }
    function ee(G) {
      var re;
      const { nodeLookup: Q, multiSelectionActive: z, nodesDraggable: B, transform: F, snapGrid: W, snapToGrid: T, selectNodesOnDrag: L, onNodeDragStart: V, onSelectionDragStart: C, unselectNodesAndEdges: A } = r();
      g = !0, (!L || !D) && !z && j && ((re = Q.get(j)) != null && re.selected || A()), D && L && j && (t == null || t(j));
      const te = ei(G.sourceEvent, { transform: F, snapGrid: W, snapToGrid: T, containerBounds: v });
      if (c = te, h = Ox(Q, B, te, j), h.size > 0 && (i || V || !j && C)) {
        const [le, ue] = Na({
          nodeId: j,
          dragItems: h,
          nodeLookup: Q
        });
        i == null || i(G.sourceEvent, h, le, ue), V == null || V(G.sourceEvent, le, ue), j || C == null || C(G.sourceEvent, ue);
      }
    }
    const U = gp().clickDistance(H).on("start", (G) => {
      const { domNode: Q, nodeDragThreshold: z, transform: B, snapGrid: F, snapToGrid: W } = r();
      v = (Q == null ? void 0 : Q.getBoundingClientRect()) || null, x = !1, S = !1, M = G.sourceEvent, z === 0 && ee(G), c = ei(G.sourceEvent, { transform: B, snapGrid: F, snapToGrid: W, containerBounds: v }), m = Ft(G.sourceEvent, v);
    }).on("drag", (G) => {
      const { autoPanOnNodeDrag: Q, transform: z, snapGrid: B, snapToGrid: F, nodeDragThreshold: W, nodeLookup: T } = r(), L = ei(G.sourceEvent, { transform: z, snapGrid: B, snapToGrid: F, containerBounds: v });
      if (M = G.sourceEvent, (G.sourceEvent.type === "touchmove" && G.sourceEvent.touches.length > 1 || // if user deletes a node while dragging, we need to abort the drag to prevent errors
      j && !T.has(j)) && (x = !0), !x) {
        if (!p && Q && g && (p = !0, K()), !g) {
          const V = Ft(G.sourceEvent, v), C = V.x - m.x, A = V.y - m.y;
          Math.sqrt(C * C + A * A) > W && ee(G);
        }
        (c.x !== L.xSnapped || c.y !== L.ySnapped) && h && g && (m = Ft(G.sourceEvent, v), b(L));
      }
    }).on("end", (G) => {
      if (!(!g || x) && (p = !1, g = !1, cancelAnimationFrame(f), h.size > 0)) {
        const { nodeLookup: Q, updateNodePositions: z, onNodeDragStop: B, onSelectionDragStop: F } = r();
        if (S && (z(h, !1), S = !1), u || B || !j && F) {
          const [W, T] = Na({
            nodeId: j,
            dragItems: h,
            nodeLookup: Q,
            dragging: !1
          });
          u == null || u(G.sourceEvent, h, W, T), B == null || B(G.sourceEvent, W, T), j || F == null || F(G.sourceEvent, T);
        }
      }
    }).filter((G) => {
      const Q = G.target;
      return !G.button && (!$ || !xh(Q, `.${$}`, E)) && (!_ || xh(Q, _, E));
    });
    y.call(U);
  }
  function k() {
    y == null || y.on(".drag", null);
  }
  return {
    update: N,
    destroy: k
  };
}
function Vx(t, r, i) {
  const l = [], u = {
    x: t.x - i,
    y: t.y - i,
    width: i * 2,
    height: i * 2
  };
  for (const c of r.values())
    ui(u, Jr(c)) > 0 && l.push(c);
  return l;
}
const Bx = 250;
function Wx(t, r, i, l) {
  var h, p;
  let u = [], c = 1 / 0;
  const f = Vx(t, i, r + Bx);
  for (const m of f) {
    const v = [...((h = m.internals.handleBounds) == null ? void 0 : h.source) ?? [], ...((p = m.internals.handleBounds) == null ? void 0 : p.target) ?? []];
    for (const g of v) {
      if (l.nodeId === g.nodeId && l.type === g.type && l.id === g.id)
        continue;
      const { x: y, y: x } = hr(m, g, g.position, !0), S = Math.sqrt(Math.pow(y - t.x, 2) + Math.pow(x - t.y, 2));
      S > r || (S < c ? (u = [{ ...g, x: y, y: x }], c = S) : S === c && u.push({ ...g, x: y, y: x }));
    }
  }
  if (!u.length)
    return null;
  if (u.length > 1) {
    const m = l.type === "source" ? "target" : "source";
    return u.find((v) => v.type === m) ?? u[0];
  }
  return u[0];
}
function Jp(t, r, i, l, u, c = !1) {
  var m, v, g;
  const f = l.get(t);
  if (!f)
    return null;
  const h = u === "strict" ? (m = f.internals.handleBounds) == null ? void 0 : m[r] : [...((v = f.internals.handleBounds) == null ? void 0 : v.source) ?? [], ...((g = f.internals.handleBounds) == null ? void 0 : g.target) ?? []], p = (i ? h == null ? void 0 : h.find((y) => y.id === i) : h == null ? void 0 : h[0]) ?? null;
  return p && c ? { ...p, ...hr(f, p, p.position, !0) } : p;
}
function eg(t, r) {
  return t || (r != null && r.classList.contains("target") ? "target" : r != null && r.classList.contains("source") ? "source" : null);
}
function Ux(t, r) {
  let i = null;
  return r ? i = !0 : t && !r && (i = !1), i;
}
const tg = () => !0;
function Yx(t, { connectionMode: r, connectionRadius: i, handleId: l, nodeId: u, edgeUpdaterType: c, isTarget: f, domNode: h, nodeLookup: p, lib: m, autoPanOnConnect: v, flowId: g, panBy: y, cancelConnection: x, onConnectStart: S, onConnect: M, onConnectEnd: N, isValidConnection: k = tg, onReconnectEnd: $, updateConnection: _, getTransform: E, getFromHandle: D, autoPanSpeed: j, dragThreshold: H = 1, handleDomNode: b }) {
  const K = Bp(t.target);
  let ee = 0, U;
  const { x: G, y: Q } = Ft(t), z = eg(c, b), B = h == null ? void 0 : h.getBoundingClientRect();
  let F = !1;
  if (!B || !z)
    return;
  const W = Jp(u, z, l, p, r);
  if (!W)
    return;
  let T = Ft(t, B), L = !1, V = null, C = !1, A = null;
  function te() {
    if (!v || !B)
      return;
    const [_e, Se] = Op(T, B, j);
    y({ x: _e, y: Se }), ee = requestAnimationFrame(te);
  }
  const re = {
    ...W,
    nodeId: u,
    type: z,
    position: W.position
  }, le = p.get(u);
  let ae = {
    inProgress: !0,
    isValid: null,
    from: hr(le, re, me.Left, !0),
    fromHandle: re,
    fromPosition: re.position,
    fromNode: le,
    to: T,
    toHandle: null,
    toPosition: uh[re.position],
    toNode: null,
    pointer: T
  };
  function J() {
    F = !0, _(ae), S == null || S(t, { nodeId: u, handleId: l, handleType: z });
  }
  H === 0 && J();
  function ce(_e) {
    if (!F) {
      const { x: We, y: It } = Ft(_e), dt = We - G, ht = It - Q;
      if (!(dt * dt + ht * ht > H * H))
        return;
      J();
    }
    if (!D() || !re) {
      xe(_e);
      return;
    }
    const Se = E();
    T = Ft(_e, B), U = Wx(yi(T, Se, !1, [1, 1]), i, p, re), L || (te(), L = !0);
    const ye = ng(_e, {
      handle: U,
      connectionMode: r,
      fromNodeId: u,
      fromHandleId: l,
      fromType: f ? "target" : "source",
      isValidConnection: k,
      doc: K,
      lib: m,
      flowId: g,
      nodeLookup: p
    });
    A = ye.handleDomNode, V = ye.connection, C = Ux(!!U, ye.isValid);
    const Ne = p.get(u), Te = Ne ? hr(Ne, re, me.Left, !0) : ae.from, Ce = {
      ...ae,
      from: Te,
      isValid: C,
      to: ye.toHandle && C ? ll({ x: ye.toHandle.x, y: ye.toHandle.y }, Se) : T,
      toHandle: ye.toHandle,
      toPosition: C && ye.toHandle ? ye.toHandle.position : uh[re.position],
      toNode: ye.toHandle ? p.get(ye.toHandle.nodeId) : null,
      pointer: T
    };
    _(Ce), ae = Ce;
  }
  function xe(_e) {
    if (!("touches" in _e && _e.touches.length > 0)) {
      if (F) {
        (U || A) && V && C && (M == null || M(V));
        const { inProgress: Se, ...ye } = ae, Ne = {
          ...ye,
          toPosition: ae.toHandle ? ae.toPosition : null
        };
        N == null || N(_e, Ne), c && ($ == null || $(_e, Ne));
      }
      x(), cancelAnimationFrame(ee), L = !1, C = !1, V = null, A = null, K.removeEventListener("mousemove", ce), K.removeEventListener("mouseup", xe), K.removeEventListener("touchmove", ce), K.removeEventListener("touchend", xe);
    }
  }
  K.addEventListener("mousemove", ce), K.addEventListener("mouseup", xe), K.addEventListener("touchmove", ce), K.addEventListener("touchend", xe);
}
function ng(t, { handle: r, connectionMode: i, fromNodeId: l, fromHandleId: u, fromType: c, doc: f, lib: h, flowId: p, isValidConnection: m = tg, nodeLookup: v }) {
  const g = c === "target", y = r ? f.querySelector(`.${h}-flow__handle[data-id="${p}-${r == null ? void 0 : r.nodeId}-${r == null ? void 0 : r.id}-${r == null ? void 0 : r.type}"]`) : null, { x, y: S } = Ft(t), M = f.elementFromPoint(x, S), N = M != null && M.classList.contains(`${h}-flow__handle`) ? M : y, k = {
    handleDomNode: N,
    isValid: !1,
    connection: null,
    toHandle: null
  };
  if (N) {
    const $ = eg(void 0, N), _ = N.getAttribute("data-nodeid"), E = N.getAttribute("data-handleid"), D = N.classList.contains("connectable"), j = N.classList.contains("connectableend");
    if (!_ || !$)
      return k;
    const H = {
      source: g ? _ : l,
      sourceHandle: g ? E : u,
      target: g ? l : _,
      targetHandle: g ? u : E
    };
    k.connection = H;
    const K = D && j && (i === Zr.Strict ? g && $ === "source" || !g && $ === "target" : _ !== l || E !== u);
    k.isValid = K && m(H), k.toHandle = Jp(_, $, E, v, i, !0);
  }
  return k;
}
const Ya = {
  onPointerDown: Yx,
  isValid: ng
};
function bx({ domNode: t, panZoom: r, getTransform: i, getViewScale: l }) {
  const u = wt(t);
  function c({ translateExtent: h, width: p, height: m, zoomStep: v = 1, pannable: g = !0, zoomable: y = !0, inversePan: x = !1 }) {
    const S = (_) => {
      if (_.sourceEvent.type !== "wheel" || !r)
        return;
      const E = i(), D = _.sourceEvent.ctrlKey && ai() ? 10 : 1, j = -_.sourceEvent.deltaY * (_.sourceEvent.deltaMode === 1 ? 0.05 : _.sourceEvent.deltaMode ? 1 : 2e-3) * v, H = E[2] * Math.pow(2, j * D);
      r.scaleTo(H);
    };
    let M = [0, 0];
    const N = (_) => {
      (_.sourceEvent.type === "mousedown" || _.sourceEvent.type === "touchstart") && (M = [
        _.sourceEvent.clientX ?? _.sourceEvent.touches[0].clientX,
        _.sourceEvent.clientY ?? _.sourceEvent.touches[0].clientY
      ]);
    }, k = (_) => {
      const E = i();
      if (_.sourceEvent.type !== "mousemove" && _.sourceEvent.type !== "touchmove" || !r)
        return;
      const D = [
        _.sourceEvent.clientX ?? _.sourceEvent.touches[0].clientX,
        _.sourceEvent.clientY ?? _.sourceEvent.touches[0].clientY
      ], j = [D[0] - M[0], D[1] - M[1]];
      M = D;
      const H = l() * Math.max(E[2], Math.log(E[2])) * (x ? -1 : 1), b = {
        x: E[0] - j[0] * H,
        y: E[1] - j[1] * H
      }, K = [
        [0, 0],
        [p, m]
      ];
      r.setViewportConstrained({
        x: b.x,
        y: b.y,
        zoom: E[2]
      }, K, h);
    }, $ = zp().on("start", N).on("zoom", g ? k : null).on("zoom.wheel", y ? S : null);
    u.call($, {});
  }
  function f() {
    u.on("zoom", null);
  }
  return {
    update: c,
    destroy: f,
    pointer: Dt
  };
}
const wl = (t) => ({
  x: t.x,
  y: t.y,
  zoom: t.k
}), Ca = ({ x: t, y: r, zoom: i }) => ml.translate(t, r).scale(i), Yr = (t, r) => t.target.closest(`.${r}`), rg = (t, r) => r === 2 && Array.isArray(t) && t.includes(2), Xx = (t) => ((t *= 2) <= 1 ? t * t * t : (t -= 2) * t * t + 2) / 2, Ma = (t, r = 0, i = Xx, l = () => {
}) => {
  const u = typeof r == "number" && r > 0;
  return u || l(), u ? t.transition().duration(r).ease(i).on("end", l) : t;
}, og = (t) => {
  const r = t.ctrlKey && ai() ? 10 : 1;
  return -t.deltaY * (t.deltaMode === 1 ? 0.05 : t.deltaMode ? 1 : 2e-3) * r;
};
function Qx({ zoomPanValues: t, noWheelClassName: r, d3Selection: i, d3Zoom: l, panOnScrollMode: u, panOnScrollSpeed: c, zoomOnPinch: f, onPanZoomStart: h, onPanZoom: p, onPanZoomEnd: m }) {
  return (v) => {
    if (Yr(v, r))
      return v.ctrlKey && v.preventDefault(), !1;
    v.preventDefault(), v.stopImmediatePropagation();
    const g = i.property("__zoom").k || 1;
    if (v.ctrlKey && f) {
      const N = Dt(v), k = og(v), $ = g * Math.pow(2, k);
      l.scaleTo(i, $, N, v);
      return;
    }
    const y = v.deltaMode === 1 ? 20 : 1;
    let x = u === ar.Vertical ? 0 : v.deltaX * y, S = u === ar.Horizontal ? 0 : v.deltaY * y;
    !ai() && v.shiftKey && u !== ar.Vertical && (x = v.deltaY * y, S = 0), l.translateBy(
      i,
      -(x / g) * c,
      -(S / g) * c,
      // @ts-ignore
      { internal: !0 }
    );
    const M = wl(i.property("__zoom"));
    clearTimeout(t.panScrollTimeout), t.isPanScrolling ? (p == null || p(v, M), t.panScrollTimeout = setTimeout(() => {
      m == null || m(v, M), t.isPanScrolling = !1;
    }, 150)) : (t.isPanScrolling = !0, h == null || h(v, M));
  };
}
function Kx({ noWheelClassName: t, preventScrolling: r, d3ZoomHandler: i }) {
  return function(l, u) {
    const c = l.type === "wheel", f = !r && c && !l.ctrlKey, h = Yr(l, t);
    if (l.ctrlKey && c && h && l.preventDefault(), f || h)
      return null;
    l.preventDefault(), i.call(this, l, u);
  };
}
function Gx({ zoomPanValues: t, onDraggingChange: r, onPanZoomStart: i }) {
  return (l) => {
    var c, f, h;
    if ((c = l.sourceEvent) != null && c.internal)
      return;
    const u = wl(l.transform);
    t.mouseButton = ((f = l.sourceEvent) == null ? void 0 : f.button) || 0, t.isZoomingOrPanning = !0, t.prevViewport = u, ((h = l.sourceEvent) == null ? void 0 : h.type) === "mousedown" && r(!0), i && (i == null || i(l.sourceEvent, u));
  };
}
function Zx({ zoomPanValues: t, panOnDrag: r, onPaneContextMenu: i, onTransformChange: l, onPanZoom: u }) {
  return (c) => {
    var f, h;
    t.usedRightMouseButton = !!(i && rg(r, t.mouseButton ?? 0)), (f = c.sourceEvent) != null && f.sync || l([c.transform.x, c.transform.y, c.transform.k]), u && !((h = c.sourceEvent) != null && h.internal) && (u == null || u(c.sourceEvent, wl(c.transform)));
  };
}
function qx({ zoomPanValues: t, panOnDrag: r, panOnScroll: i, onDraggingChange: l, onPanZoomEnd: u, onPaneContextMenu: c }) {
  return (f) => {
    var h;
    if (!((h = f.sourceEvent) != null && h.internal) && (t.isZoomingOrPanning = !1, c && rg(r, t.mouseButton ?? 0) && !t.usedRightMouseButton && f.sourceEvent && c(f.sourceEvent), t.usedRightMouseButton = !1, l(!1), u)) {
      const p = wl(f.transform);
      t.prevViewport = p, clearTimeout(t.timerId), t.timerId = setTimeout(
        () => {
          u == null || u(f.sourceEvent, p);
        },
        // we need a setTimeout for panOnScroll to suppress multiple end events fired during scroll
        i ? 150 : 0
      );
    }
  };
}
function Jx({ zoomActivationKeyPressed: t, zoomOnScroll: r, zoomOnPinch: i, panOnDrag: l, panOnScroll: u, zoomOnDoubleClick: c, userSelectionActive: f, noWheelClassName: h, noPanClassName: p, lib: m, connectionInProgress: v }) {
  return (g) => {
    var N;
    const y = t || r, x = i && g.ctrlKey, S = g.type === "wheel";
    if (g.button === 1 && g.type === "mousedown" && (Yr(g, `${m}-flow__node`) || Yr(g, `${m}-flow__edge`)))
      return !0;
    if (!l && !y && !u && !c && !i || f || v && !S || Yr(g, h) && S || Yr(g, p) && (!S || u && S && !t) || !i && g.ctrlKey && S)
      return !1;
    if (!i && g.type === "touchstart" && ((N = g.touches) == null ? void 0 : N.length) > 1)
      return g.preventDefault(), !1;
    if (!y && !u && !x && S || !l && (g.type === "mousedown" || g.type === "touchstart") || Array.isArray(l) && !l.includes(g.button) && g.type === "mousedown")
      return !1;
    const M = Array.isArray(l) && l.includes(g.button) || !g.button || g.button <= 1;
    return (!g.ctrlKey || S) && M;
  };
}
function e1({ domNode: t, minZoom: r, maxZoom: i, translateExtent: l, viewport: u, onPanZoom: c, onPanZoomStart: f, onPanZoomEnd: h, onDraggingChange: p }) {
  const m = {
    isZoomingOrPanning: !1,
    usedRightMouseButton: !1,
    prevViewport: {},
    mouseButton: 0,
    timerId: void 0,
    panScrollTimeout: void 0,
    isPanScrolling: !1
  }, v = t.getBoundingClientRect(), g = zp().scaleExtent([r, i]).translateExtent(l), y = wt(t).call(g);
  $({
    x: u.x,
    y: u.y,
    zoom: qr(u.zoom, r, i)
  }, [
    [0, 0],
    [v.width, v.height]
  ], l);
  const x = y.on("wheel.zoom"), S = y.on("dblclick.zoom");
  g.wheelDelta(og);
  function M(U, G) {
    return y ? new Promise((Q) => {
      g == null || g.interpolate((G == null ? void 0 : G.interpolate) === "linear" ? Jo : Ks).transform(Ma(y, G == null ? void 0 : G.duration, G == null ? void 0 : G.ease, () => Q(!0)), U);
    }) : Promise.resolve(!1);
  }
  function N({ noWheelClassName: U, noPanClassName: G, onPaneContextMenu: Q, userSelectionActive: z, panOnScroll: B, panOnDrag: F, panOnScrollMode: W, panOnScrollSpeed: T, preventScrolling: L, zoomOnPinch: V, zoomOnScroll: C, zoomOnDoubleClick: A, zoomActivationKeyPressed: te, lib: re, onTransformChange: le, connectionInProgress: ue, paneClickDistance: ae, selectionOnDrag: J }) {
    z && !m.isZoomingOrPanning && k();
    const ce = B && !te && !z;
    g.clickDistance(J ? 1 / 0 : !Ot(ae) || ae < 0 ? 0 : ae);
    const xe = ce ? Qx({
      zoomPanValues: m,
      noWheelClassName: U,
      d3Selection: y,
      d3Zoom: g,
      panOnScrollMode: W,
      panOnScrollSpeed: T,
      zoomOnPinch: V,
      onPanZoomStart: f,
      onPanZoom: c,
      onPanZoomEnd: h
    }) : Kx({
      noWheelClassName: U,
      preventScrolling: L,
      d3ZoomHandler: x
    });
    if (y.on("wheel.zoom", xe, { passive: !1 }), !z) {
      const Se = Gx({
        zoomPanValues: m,
        onDraggingChange: p,
        onPanZoomStart: f
      });
      g.on("start", Se);
      const ye = Zx({
        zoomPanValues: m,
        panOnDrag: F,
        onPaneContextMenu: !!Q,
        onPanZoom: c,
        onTransformChange: le
      });
      g.on("zoom", ye);
      const Ne = qx({
        zoomPanValues: m,
        panOnDrag: F,
        panOnScroll: B,
        onPaneContextMenu: Q,
        onPanZoomEnd: h,
        onDraggingChange: p
      });
      g.on("end", Ne);
    }
    const _e = Jx({
      zoomActivationKeyPressed: te,
      panOnDrag: F,
      zoomOnScroll: C,
      panOnScroll: B,
      zoomOnDoubleClick: A,
      zoomOnPinch: V,
      userSelectionActive: z,
      noPanClassName: G,
      noWheelClassName: U,
      lib: re,
      connectionInProgress: ue
    });
    g.filter(_e), A ? y.on("dblclick.zoom", S) : y.on("dblclick.zoom", null);
  }
  function k() {
    g.on("zoom", null);
  }
  async function $(U, G, Q) {
    const z = Ca(U), B = g == null ? void 0 : g.constrain()(z, G, Q);
    return B && await M(B), new Promise((F) => F(B));
  }
  async function _(U, G) {
    const Q = Ca(U);
    return await M(Q, G), new Promise((z) => z(Q));
  }
  function E(U) {
    if (y) {
      const G = Ca(U), Q = y.property("__zoom");
      (Q.k !== U.zoom || Q.x !== U.x || Q.y !== U.y) && (g == null || g.transform(y, G, null, { sync: !0 }));
    }
  }
  function D() {
    const U = y ? Ip(y.node()) : { x: 0, y: 0, k: 1 };
    return { x: U.x, y: U.y, zoom: U.k };
  }
  function j(U, G) {
    return y ? new Promise((Q) => {
      g == null || g.interpolate((G == null ? void 0 : G.interpolate) === "linear" ? Jo : Ks).scaleTo(Ma(y, G == null ? void 0 : G.duration, G == null ? void 0 : G.ease, () => Q(!0)), U);
    }) : Promise.resolve(!1);
  }
  function H(U, G) {
    return y ? new Promise((Q) => {
      g == null || g.interpolate((G == null ? void 0 : G.interpolate) === "linear" ? Jo : Ks).scaleBy(Ma(y, G == null ? void 0 : G.duration, G == null ? void 0 : G.ease, () => Q(!0)), U);
    }) : Promise.resolve(!1);
  }
  function b(U) {
    g == null || g.scaleExtent(U);
  }
  function K(U) {
    g == null || g.translateExtent(U);
  }
  function ee(U) {
    const G = !Ot(U) || U < 0 ? 0 : U;
    g == null || g.clickDistance(G);
  }
  return {
    update: N,
    destroy: k,
    setViewport: _,
    setViewportConstrained: $,
    getViewport: D,
    scaleTo: j,
    scaleBy: H,
    setScaleExtent: b,
    setTranslateExtent: K,
    syncViewport: E,
    setClickDistance: ee
  };
}
var to;
(function(t) {
  t.Line = "line", t.Handle = "handle";
})(to || (to = {}));
function t1({ width: t, prevWidth: r, height: i, prevHeight: l, affectsX: u, affectsY: c }) {
  const f = t - r, h = i - l, p = [f > 0 ? 1 : f < 0 ? -1 : 0, h > 0 ? 1 : h < 0 ? -1 : 0];
  return f && u && (p[0] = p[0] * -1), h && c && (p[1] = p[1] * -1), p;
}
function Sh(t) {
  const r = t.includes("right") || t.includes("left"), i = t.includes("bottom") || t.includes("top"), l = t.includes("left"), u = t.includes("top");
  return {
    isHorizontal: r,
    isVertical: i,
    affectsX: l,
    affectsY: u
  };
}
function Dn(t, r) {
  return Math.max(0, r - t);
}
function jn(t, r) {
  return Math.max(0, t - r);
}
function Us(t, r, i) {
  return Math.max(0, r - t, t - i);
}
function _h(t, r) {
  return t ? !r : r;
}
function n1(t, r, i, l, u, c, f, h) {
  let { affectsX: p, affectsY: m } = r;
  const { isHorizontal: v, isVertical: g } = r, y = v && g, { xSnapped: x, ySnapped: S } = i, { minWidth: M, maxWidth: N, minHeight: k, maxHeight: $ } = l, { x: _, y: E, width: D, height: j, aspectRatio: H } = t;
  let b = Math.floor(v ? x - t.pointerX : 0), K = Math.floor(g ? S - t.pointerY : 0);
  const ee = D + (p ? -b : b), U = j + (m ? -K : K), G = -c[0] * D, Q = -c[1] * j;
  let z = Us(ee, M, N), B = Us(U, k, $);
  if (f) {
    let T = 0, L = 0;
    p && b < 0 ? T = Dn(_ + b + G, f[0][0]) : !p && b > 0 && (T = jn(_ + ee + G, f[1][0])), m && K < 0 ? L = Dn(E + K + Q, f[0][1]) : !m && K > 0 && (L = jn(E + U + Q, f[1][1])), z = Math.max(z, T), B = Math.max(B, L);
  }
  if (h) {
    let T = 0, L = 0;
    p && b > 0 ? T = jn(_ + b, h[0][0]) : !p && b < 0 && (T = Dn(_ + ee, h[1][0])), m && K > 0 ? L = jn(E + K, h[0][1]) : !m && K < 0 && (L = Dn(E + U, h[1][1])), z = Math.max(z, T), B = Math.max(B, L);
  }
  if (u) {
    if (v) {
      const T = Us(ee / H, k, $) * H;
      if (z = Math.max(z, T), f) {
        let L = 0;
        !p && !m || p && !m && y ? L = jn(E + Q + ee / H, f[1][1]) * H : L = Dn(E + Q + (p ? b : -b) / H, f[0][1]) * H, z = Math.max(z, L);
      }
      if (h) {
        let L = 0;
        !p && !m || p && !m && y ? L = Dn(E + ee / H, h[1][1]) * H : L = jn(E + (p ? b : -b) / H, h[0][1]) * H, z = Math.max(z, L);
      }
    }
    if (g) {
      const T = Us(U * H, M, N) / H;
      if (B = Math.max(B, T), f) {
        let L = 0;
        !p && !m || m && !p && y ? L = jn(_ + U * H + G, f[1][0]) / H : L = Dn(_ + (m ? K : -K) * H + G, f[0][0]) / H, B = Math.max(B, L);
      }
      if (h) {
        let L = 0;
        !p && !m || m && !p && y ? L = Dn(_ + U * H, h[1][0]) / H : L = jn(_ + (m ? K : -K) * H, h[0][0]) / H, B = Math.max(B, L);
      }
    }
  }
  K = K + (K < 0 ? B : -B), b = b + (b < 0 ? z : -z), u && (y ? ee > U * H ? K = (_h(p, m) ? -b : b) / H : b = (_h(p, m) ? -K : K) * H : v ? (K = b / H, m = p) : (b = K * H, p = m));
  const F = p ? _ + b : _, W = m ? E + K : E;
  return {
    width: D + (p ? -b : b),
    height: j + (m ? -K : K),
    x: c[0] * b * (p ? -1 : 1) + F,
    y: c[1] * K * (m ? -1 : 1) + W
  };
}
const ig = { width: 0, height: 0, x: 0, y: 0 }, r1 = {
  ...ig,
  pointerX: 0,
  pointerY: 0,
  aspectRatio: 1
};
function o1(t) {
  return [
    [0, 0],
    [t.measured.width, t.measured.height]
  ];
}
function i1(t, r, i) {
  const l = r.position.x + t.position.x, u = r.position.y + t.position.y, c = t.measured.width ?? 0, f = t.measured.height ?? 0, h = i[0] * c, p = i[1] * f;
  return [
    [l - h, u - p],
    [l + c - h, u + f - p]
  ];
}
function s1({ domNode: t, nodeId: r, getStoreItems: i, onChange: l, onEnd: u }) {
  const c = wt(t);
  let f = {
    controlDirection: Sh("bottom-right"),
    boundaries: {
      minWidth: 0,
      minHeight: 0,
      maxWidth: Number.MAX_VALUE,
      maxHeight: Number.MAX_VALUE
    },
    resizeDirection: void 0,
    keepAspectRatio: !1
  };
  function h({ controlPosition: m, boundaries: v, keepAspectRatio: g, resizeDirection: y, onResizeStart: x, onResize: S, onResizeEnd: M, shouldResize: N }) {
    let k = { ...ig }, $ = { ...r1 };
    f = {
      boundaries: v,
      resizeDirection: y,
      keepAspectRatio: g,
      controlDirection: Sh(m)
    };
    let _, E = null, D = [], j, H, b, K = !1;
    const ee = gp().on("start", (U) => {
      const { nodeLookup: G, transform: Q, snapGrid: z, snapToGrid: B, nodeOrigin: F, paneDomNode: W } = i();
      if (_ = G.get(r), !_)
        return;
      E = (W == null ? void 0 : W.getBoundingClientRect()) ?? null;
      const { xSnapped: T, ySnapped: L } = ei(U.sourceEvent, {
        transform: Q,
        snapGrid: z,
        snapToGrid: B,
        containerBounds: E
      });
      k = {
        width: _.measured.width ?? 0,
        height: _.measured.height ?? 0,
        x: _.position.x ?? 0,
        y: _.position.y ?? 0
      }, $ = {
        ...k,
        pointerX: T,
        pointerY: L,
        aspectRatio: k.width / k.height
      }, j = void 0, _.parentId && (_.extent === "parent" || _.expandParent) && (j = G.get(_.parentId), H = j && _.extent === "parent" ? o1(j) : void 0), D = [], b = void 0;
      for (const [V, C] of G)
        if (C.parentId === r && (D.push({
          id: V,
          position: { ...C.position },
          extent: C.extent
        }), C.extent === "parent" || C.expandParent)) {
          const A = i1(C, _, C.origin ?? F);
          b ? b = [
            [Math.min(A[0][0], b[0][0]), Math.min(A[0][1], b[0][1])],
            [Math.max(A[1][0], b[1][0]), Math.max(A[1][1], b[1][1])]
          ] : b = A;
        }
      x == null || x(U, { ...k });
    }).on("drag", (U) => {
      const { transform: G, snapGrid: Q, snapToGrid: z, nodeOrigin: B } = i(), F = ei(U.sourceEvent, {
        transform: G,
        snapGrid: Q,
        snapToGrid: z,
        containerBounds: E
      }), W = [];
      if (!_)
        return;
      const { x: T, y: L, width: V, height: C } = k, A = {}, te = _.origin ?? B, { width: re, height: le, x: ue, y: ae } = n1($, f.controlDirection, F, f.boundaries, f.keepAspectRatio, te, H, b), J = re !== V, ce = le !== C, xe = ue !== T && J, _e = ae !== L && ce;
      if (!xe && !_e && !J && !ce)
        return;
      if ((xe || _e || te[0] === 1 || te[1] === 1) && (A.x = xe ? ue : k.x, A.y = _e ? ae : k.y, k.x = A.x, k.y = A.y, D.length > 0)) {
        const Te = ue - T, Ce = ae - L;
        for (const We of D)
          We.position = {
            x: We.position.x - Te + te[0] * (re - V),
            y: We.position.y - Ce + te[1] * (le - C)
          }, W.push(We);
      }
      if ((J || ce) && (A.width = J && (!f.resizeDirection || f.resizeDirection === "horizontal") ? re : k.width, A.height = ce && (!f.resizeDirection || f.resizeDirection === "vertical") ? le : k.height, k.width = A.width, k.height = A.height), j && _.expandParent) {
        const Te = te[0] * (A.width ?? 0);
        A.x && A.x < Te && (k.x = Te, $.x = $.x - (A.x - Te));
        const Ce = te[1] * (A.height ?? 0);
        A.y && A.y < Ce && (k.y = Ce, $.y = $.y - (A.y - Ce));
      }
      const Se = t1({
        width: k.width,
        prevWidth: V,
        height: k.height,
        prevHeight: C,
        affectsX: f.controlDirection.affectsX,
        affectsY: f.controlDirection.affectsY
      }), ye = { ...k, direction: Se };
      (N == null ? void 0 : N(U, ye)) !== !1 && (K = !0, S == null || S(U, ye), l(A, W));
    }).on("end", (U) => {
      K && (M == null || M(U, { ...k }), u == null || u({ ...k }), K = !1);
    });
    c.call(ee);
  }
  function p() {
    c.on(".drag", null);
  }
  return {
    update: h,
    destroy: p
  };
}
var Pa = { exports: {} }, Ia = {}, za = { exports: {} }, Ta = {};
/**
 * @license React
 * use-sync-external-store-shim.production.js
 *
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var Eh;
function l1() {
  if (Eh) return Ta;
  Eh = 1;
  var t = fi();
  function r(g, y) {
    return g === y && (g !== 0 || 1 / g === 1 / y) || g !== g && y !== y;
  }
  var i = typeof Object.is == "function" ? Object.is : r, l = t.useState, u = t.useEffect, c = t.useLayoutEffect, f = t.useDebugValue;
  function h(g, y) {
    var x = y(), S = l({ inst: { value: x, getSnapshot: y } }), M = S[0].inst, N = S[1];
    return c(
      function() {
        M.value = x, M.getSnapshot = y, p(M) && N({ inst: M });
      },
      [g, x, y]
    ), u(
      function() {
        return p(M) && N({ inst: M }), g(function() {
          p(M) && N({ inst: M });
        });
      },
      [g]
    ), f(x), x;
  }
  function p(g) {
    var y = g.getSnapshot;
    g = g.value;
    try {
      var x = y();
      return !i(g, x);
    } catch {
      return !0;
    }
  }
  function m(g, y) {
    return y();
  }
  var v = typeof window > "u" || typeof window.document > "u" || typeof window.document.createElement > "u" ? m : h;
  return Ta.useSyncExternalStore = t.useSyncExternalStore !== void 0 ? t.useSyncExternalStore : v, Ta;
}
var kh;
function u1() {
  return kh || (kh = 1, za.exports = l1()), za.exports;
}
/**
 * @license React
 * use-sync-external-store-shim/with-selector.production.js
 *
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var Nh;
function a1() {
  if (Nh) return Ia;
  Nh = 1;
  var t = fi(), r = u1();
  function i(m, v) {
    return m === v && (m !== 0 || 1 / m === 1 / v) || m !== m && v !== v;
  }
  var l = typeof Object.is == "function" ? Object.is : i, u = r.useSyncExternalStore, c = t.useRef, f = t.useEffect, h = t.useMemo, p = t.useDebugValue;
  return Ia.useSyncExternalStoreWithSelector = function(m, v, g, y, x) {
    var S = c(null);
    if (S.current === null) {
      var M = { hasValue: !1, value: null };
      S.current = M;
    } else M = S.current;
    S = h(
      function() {
        function k(j) {
          if (!$) {
            if ($ = !0, _ = j, j = y(j), x !== void 0 && M.hasValue) {
              var H = M.value;
              if (x(H, j))
                return E = H;
            }
            return E = j;
          }
          if (H = E, l(_, j)) return H;
          var b = y(j);
          return x !== void 0 && x(H, b) ? (_ = j, H) : (_ = j, E = b);
        }
        var $ = !1, _, E, D = g === void 0 ? null : g;
        return [
          function() {
            return k(v());
          },
          D === null ? void 0 : function() {
            return k(D());
          }
        ];
      },
      [v, g, y, x]
    );
    var N = u(m, S[0], S[1]);
    return f(
      function() {
        M.hasValue = !0, M.value = N;
      },
      [N]
    ), p(N), N;
  }, Ia;
}
var Ch;
function c1() {
  return Ch || (Ch = 1, Pa.exports = a1()), Pa.exports;
}
var f1 = c1();
const d1 = /* @__PURE__ */ Jh(f1), h1 = {}, Mh = (t) => {
  let r;
  const i = /* @__PURE__ */ new Set(), l = (v, g) => {
    const y = typeof v == "function" ? v(r) : v;
    if (!Object.is(y, r)) {
      const x = r;
      r = g ?? (typeof y != "object" || y === null) ? y : Object.assign({}, r, y), i.forEach((S) => S(r, x));
    }
  }, u = () => r, p = { setState: l, getState: u, getInitialState: () => m, subscribe: (v) => (i.add(v), () => i.delete(v)), destroy: () => {
    (h1 ? "production" : void 0) !== "production" && console.warn(
      "[DEPRECATED] The `destroy` method will be unsupported in a future version. Instead use unsubscribe function returned by subscribe. Everything will be garbage-collected if store is garbage-collected."
    ), i.clear();
  } }, m = r = t(l, u, p);
  return p;
}, p1 = (t) => t ? Mh(t) : Mh, { useDebugValue: g1 } = E0, { useSyncExternalStoreWithSelector: m1 } = d1, y1 = (t) => t;
function sg(t, r = y1, i) {
  const l = m1(
    t.subscribe,
    t.getState,
    t.getServerState || t.getInitialState,
    r,
    i
  );
  return g1(l), l;
}
const Ph = (t, r) => {
  const i = p1(t), l = (u, c = r) => sg(i, u, c);
  return Object.assign(l, i), l;
}, v1 = (t, r) => t ? Ph(t, r) : Ph;
function $e(t, r) {
  if (Object.is(t, r))
    return !0;
  if (typeof t != "object" || t === null || typeof r != "object" || r === null)
    return !1;
  if (t instanceof Map && r instanceof Map) {
    if (t.size !== r.size) return !1;
    for (const [l, u] of t)
      if (!Object.is(u, r.get(l)))
        return !1;
    return !0;
  }
  if (t instanceof Set && r instanceof Set) {
    if (t.size !== r.size) return !1;
    for (const l of t)
      if (!r.has(l))
        return !1;
    return !0;
  }
  const i = Object.keys(t);
  if (i.length !== Object.keys(r).length)
    return !1;
  for (const l of i)
    if (!Object.prototype.hasOwnProperty.call(r, l) || !Object.is(t[l], r[l]))
      return !1;
  return !0;
}
ep();
const xl = q.createContext(null), w1 = xl.Provider, lg = Kt.error001();
function Me(t, r) {
  const i = q.useContext(xl);
  if (i === null)
    throw new Error(lg);
  return sg(i, t, r);
}
function je() {
  const t = q.useContext(xl);
  if (t === null)
    throw new Error(lg);
  return q.useMemo(() => ({
    getState: t.getState,
    setState: t.setState,
    subscribe: t.subscribe
  }), [t]);
}
const Ih = { display: "none" }, x1 = {
  position: "absolute",
  width: 1,
  height: 1,
  margin: -1,
  border: 0,
  padding: 0,
  overflow: "hidden",
  clip: "rect(0px, 0px, 0px, 0px)",
  clipPath: "inset(100%)"
}, ug = "react-flow__node-desc", ag = "react-flow__edge-desc", S1 = "react-flow__aria-live", _1 = (t) => t.ariaLiveMessage, E1 = (t) => t.ariaLabelConfig;
function k1({ rfId: t }) {
  const r = Me(_1);
  return I.jsx("div", { id: `${S1}-${t}`, "aria-live": "assertive", "aria-atomic": "true", style: x1, children: r });
}
function N1({ rfId: t, disableKeyboardA11y: r }) {
  const i = Me(E1);
  return I.jsxs(I.Fragment, { children: [I.jsx("div", { id: `${ug}-${t}`, style: Ih, children: r ? i["node.a11yDescription.default"] : i["node.a11yDescription.keyboardDisabled"] }), I.jsx("div", { id: `${ag}-${t}`, style: Ih, children: i["edge.a11yDescription.default"] }), !r && I.jsx(k1, { rfId: t })] });
}
const Sl = q.forwardRef(({ position: t = "top-left", children: r, className: i, style: l, ...u }, c) => {
  const f = `${t}`.split("-");
  return I.jsx("div", { className: Ye(["react-flow__panel", i, ...f]), style: l, ref: c, ...u, children: r });
});
Sl.displayName = "Panel";
function C1({ proOptions: t, position: r = "bottom-right" }) {
  return t != null && t.hideAttribution ? null : I.jsx(Sl, { position: r, className: "react-flow__attribution", "data-message": "Please only hide this attribution when you are subscribed to React Flow Pro: https://pro.reactflow.dev", children: I.jsx("a", { href: "https://reactflow.dev", target: "_blank", rel: "noopener noreferrer", "aria-label": "React Flow attribution", children: "React Flow" }) });
}
const M1 = (t) => {
  const r = [], i = [];
  for (const [, l] of t.nodeLookup)
    l.selected && r.push(l.internals.userNode);
  for (const [, l] of t.edgeLookup)
    l.selected && i.push(l);
  return { selectedNodes: r, selectedEdges: i };
}, Ys = (t) => t.id;
function P1(t, r) {
  return $e(t.selectedNodes.map(Ys), r.selectedNodes.map(Ys)) && $e(t.selectedEdges.map(Ys), r.selectedEdges.map(Ys));
}
function I1({ onSelectionChange: t }) {
  const r = je(), { selectedNodes: i, selectedEdges: l } = Me(M1, P1);
  return q.useEffect(() => {
    const u = { nodes: i, edges: l };
    t == null || t(u), r.getState().onSelectionChangeHandlers.forEach((c) => c(u));
  }, [i, l, t]), null;
}
const z1 = (t) => !!t.onSelectionChangeHandlers;
function T1({ onSelectionChange: t }) {
  const r = Me(z1);
  return t || r ? I.jsx(I1, { onSelectionChange: t }) : null;
}
const ba = typeof window < "u" ? q.useLayoutEffect : q.useEffect, cg = [0, 0], L1 = { x: 0, y: 0, zoom: 1 }, A1 = [
  "nodes",
  "edges",
  "defaultNodes",
  "defaultEdges",
  "onConnect",
  "onConnectStart",
  "onConnectEnd",
  "onClickConnectStart",
  "onClickConnectEnd",
  "nodesDraggable",
  "autoPanOnNodeFocus",
  "nodesConnectable",
  "nodesFocusable",
  "edgesFocusable",
  "edgesReconnectable",
  "elevateNodesOnSelect",
  "elevateEdgesOnSelect",
  "minZoom",
  "maxZoom",
  "nodeExtent",
  "onNodesChange",
  "onEdgesChange",
  "elementsSelectable",
  "connectionMode",
  "snapGrid",
  "snapToGrid",
  "translateExtent",
  "connectOnClick",
  "defaultEdgeOptions",
  "fitView",
  "fitViewOptions",
  "onNodesDelete",
  "onEdgesDelete",
  "onDelete",
  "onNodeDrag",
  "onNodeDragStart",
  "onNodeDragStop",
  "onSelectionDrag",
  "onSelectionDragStart",
  "onSelectionDragStop",
  "onMoveStart",
  "onMove",
  "onMoveEnd",
  "noPanClassName",
  "nodeOrigin",
  "autoPanOnConnect",
  "autoPanOnNodeDrag",
  "onError",
  "connectionRadius",
  "isValidConnection",
  "selectNodesOnDrag",
  "nodeDragThreshold",
  "connectionDragThreshold",
  "onBeforeDelete",
  "debug",
  "autoPanSpeed",
  "ariaLabelConfig",
  "zIndexMode"
], zh = [...A1, "rfId"], R1 = (t) => ({
  setNodes: t.setNodes,
  setEdges: t.setEdges,
  setMinZoom: t.setMinZoom,
  setMaxZoom: t.setMaxZoom,
  setTranslateExtent: t.setTranslateExtent,
  setNodeExtent: t.setNodeExtent,
  reset: t.reset,
  setDefaultNodesAndEdges: t.setDefaultNodesAndEdges
}), Th = {
  /*
   * these are values that are also passed directly to other components
   * than the StoreUpdater. We can reduce the number of setStore calls
   * by setting the same values here as prev fields.
   */
  translateExtent: ii,
  nodeOrigin: cg,
  minZoom: 0.5,
  maxZoom: 2,
  elementsSelectable: !0,
  noPanClassName: "nopan",
  rfId: "1"
};
function $1(t) {
  const { setNodes: r, setEdges: i, setMinZoom: l, setMaxZoom: u, setTranslateExtent: c, setNodeExtent: f, reset: h, setDefaultNodesAndEdges: p } = Me(R1, $e), m = je();
  ba(() => (p(t.defaultNodes, t.defaultEdges), () => {
    v.current = Th, h();
  }), []);
  const v = q.useRef(Th);
  return ba(
    () => {
      for (const g of zh) {
        const y = t[g], x = v.current[g];
        y !== x && (typeof t[g] > "u" || (g === "nodes" ? r(y) : g === "edges" ? i(y) : g === "minZoom" ? l(y) : g === "maxZoom" ? u(y) : g === "translateExtent" ? c(y) : g === "nodeExtent" ? f(y) : g === "ariaLabelConfig" ? m.setState({ ariaLabelConfig: vx(y) }) : g === "fitView" ? m.setState({ fitViewQueued: y }) : g === "fitViewOptions" ? m.setState({ fitViewOptions: y }) : m.setState({ [g]: y })));
      }
      v.current = t;
    },
    // Only re-run the effect if one of the fields we track changes
    zh.map((g) => t[g])
  ), null;
}
function Lh() {
  return typeof window > "u" || !window.matchMedia ? null : window.matchMedia("(prefers-color-scheme: dark)");
}
function D1(t) {
  var l;
  const [r, i] = q.useState(t === "system" ? null : t);
  return q.useEffect(() => {
    if (t !== "system") {
      i(t);
      return;
    }
    const u = Lh(), c = () => i(u != null && u.matches ? "dark" : "light");
    return c(), u == null || u.addEventListener("change", c), () => {
      u == null || u.removeEventListener("change", c);
    };
  }, [t]), r !== null ? r : (l = Lh()) != null && l.matches ? "dark" : "light";
}
const Ah = typeof document < "u" ? document : null;
function ci(t = null, r = { target: Ah, actInsideInputWithModifier: !0 }) {
  const [i, l] = q.useState(!1), u = q.useRef(!1), c = q.useRef(/* @__PURE__ */ new Set([])), [f, h] = q.useMemo(() => {
    if (t !== null) {
      const m = (Array.isArray(t) ? t : [t]).filter((g) => typeof g == "string").map((g) => g.replace("+", `
`).replace(`

`, `
+`).split(`
`)), v = m.reduce((g, y) => g.concat(...y), []);
      return [m, v];
    }
    return [[], []];
  }, [t]);
  return q.useEffect(() => {
    const p = (r == null ? void 0 : r.target) ?? Ah, m = (r == null ? void 0 : r.actInsideInputWithModifier) ?? !0;
    if (t !== null) {
      const v = (x) => {
        var N, k;
        if (u.current = x.ctrlKey || x.metaKey || x.shiftKey || x.altKey, (!u.current || u.current && !m) && Wp(x))
          return !1;
        const M = $h(x.code, h);
        if (c.current.add(x[M]), Rh(f, c.current, !1)) {
          const $ = ((k = (N = x.composedPath) == null ? void 0 : N.call(x)) == null ? void 0 : k[0]) || x.target, _ = ($ == null ? void 0 : $.nodeName) === "BUTTON" || ($ == null ? void 0 : $.nodeName) === "A";
          r.preventDefault !== !1 && (u.current || !_) && x.preventDefault(), l(!0);
        }
      }, g = (x) => {
        const S = $h(x.code, h);
        Rh(f, c.current, !0) ? (l(!1), c.current.clear()) : c.current.delete(x[S]), x.key === "Meta" && c.current.clear(), u.current = !1;
      }, y = () => {
        c.current.clear(), l(!1);
      };
      return p == null || p.addEventListener("keydown", v), p == null || p.addEventListener("keyup", g), window.addEventListener("blur", y), window.addEventListener("contextmenu", y), () => {
        p == null || p.removeEventListener("keydown", v), p == null || p.removeEventListener("keyup", g), window.removeEventListener("blur", y), window.removeEventListener("contextmenu", y);
      };
    }
  }, [t, l]), i;
}
function Rh(t, r, i) {
  return t.filter((l) => i || l.length === r.size).some((l) => l.every((u) => r.has(u)));
}
function $h(t, r) {
  return r.includes(t) ? "code" : "key";
}
const j1 = () => {
  const t = je();
  return q.useMemo(() => ({
    zoomIn: (r) => {
      const { panZoom: i } = t.getState();
      return i ? i.scaleBy(1.2, r) : Promise.resolve(!1);
    },
    zoomOut: (r) => {
      const { panZoom: i } = t.getState();
      return i ? i.scaleBy(1 / 1.2, r) : Promise.resolve(!1);
    },
    zoomTo: (r, i) => {
      const { panZoom: l } = t.getState();
      return l ? l.scaleTo(r, i) : Promise.resolve(!1);
    },
    getZoom: () => t.getState().transform[2],
    setViewport: async (r, i) => {
      const { transform: [l, u, c], panZoom: f } = t.getState();
      return f ? (await f.setViewport({
        x: r.x ?? l,
        y: r.y ?? u,
        zoom: r.zoom ?? c
      }, i), Promise.resolve(!0)) : Promise.resolve(!1);
    },
    getViewport: () => {
      const [r, i, l] = t.getState().transform;
      return { x: r, y: i, zoom: l };
    },
    setCenter: async (r, i, l) => t.getState().setCenter(r, i, l),
    fitBounds: async (r, i) => {
      const { width: l, height: u, minZoom: c, maxZoom: f, panZoom: h } = t.getState(), p = oc(r, l, u, c, f, (i == null ? void 0 : i.padding) ?? 0.1);
      return h ? (await h.setViewport(p, {
        duration: i == null ? void 0 : i.duration,
        ease: i == null ? void 0 : i.ease,
        interpolate: i == null ? void 0 : i.interpolate
      }), Promise.resolve(!0)) : Promise.resolve(!1);
    },
    screenToFlowPosition: (r, i = {}) => {
      const { transform: l, snapGrid: u, snapToGrid: c, domNode: f } = t.getState();
      if (!f)
        return r;
      const { x: h, y: p } = f.getBoundingClientRect(), m = {
        x: r.x - h,
        y: r.y - p
      }, v = i.snapGrid ?? u, g = i.snapToGrid ?? c;
      return yi(m, l, g, v);
    },
    flowToScreenPosition: (r) => {
      const { transform: i, domNode: l } = t.getState();
      if (!l)
        return r;
      const { x: u, y: c } = l.getBoundingClientRect(), f = ll(r, i);
      return {
        x: f.x + u,
        y: f.y + c
      };
    }
  }), []);
};
function fg(t, r) {
  const i = [], l = /* @__PURE__ */ new Map(), u = [];
  for (const c of t)
    if (c.type === "add") {
      u.push(c);
      continue;
    } else if (c.type === "remove" || c.type === "replace")
      l.set(c.id, [c]);
    else {
      const f = l.get(c.id);
      f ? f.push(c) : l.set(c.id, [c]);
    }
  for (const c of r) {
    const f = l.get(c.id);
    if (!f) {
      i.push(c);
      continue;
    }
    if (f[0].type === "remove")
      continue;
    if (f[0].type === "replace") {
      i.push({ ...f[0].item });
      continue;
    }
    const h = { ...c };
    for (const p of f)
      O1(p, h);
    i.push(h);
  }
  return u.length && u.forEach((c) => {
    c.index !== void 0 ? i.splice(c.index, 0, { ...c.item }) : i.push({ ...c.item });
  }), i;
}
function O1(t, r) {
  switch (t.type) {
    case "select": {
      r.selected = t.selected;
      break;
    }
    case "position": {
      typeof t.position < "u" && (r.position = t.position), typeof t.dragging < "u" && (r.dragging = t.dragging);
      break;
    }
    case "dimensions": {
      typeof t.dimensions < "u" && (r.measured = {
        ...t.dimensions
      }, t.setAttributes && ((t.setAttributes === !0 || t.setAttributes === "width") && (r.width = t.dimensions.width), (t.setAttributes === !0 || t.setAttributes === "height") && (r.height = t.dimensions.height))), typeof t.resizing == "boolean" && (r.resizing = t.resizing);
      break;
    }
  }
}
function F1(t, r) {
  return fg(t, r);
}
function H1(t, r) {
  return fg(t, r);
}
function sr(t, r) {
  return {
    id: t,
    type: "select",
    selected: r
  };
}
function br(t, r = /* @__PURE__ */ new Set(), i = !1) {
  const l = [];
  for (const [u, c] of t) {
    const f = r.has(u);
    !(c.selected === void 0 && !f) && c.selected !== f && (i && (c.selected = f), l.push(sr(c.id, f)));
  }
  return l;
}
function Dh({ items: t = [], lookup: r }) {
  var u;
  const i = [], l = new Map(t.map((c) => [c.id, c]));
  for (const [c, f] of t.entries()) {
    const h = r.get(f.id), p = ((u = h == null ? void 0 : h.internals) == null ? void 0 : u.userNode) ?? h;
    p !== void 0 && p !== f && i.push({ id: f.id, item: f, type: "replace" }), p === void 0 && i.push({ item: f, type: "add", index: c });
  }
  for (const [c] of r)
    l.get(c) === void 0 && i.push({ id: c, type: "remove" });
  return i;
}
function jh(t) {
  return {
    id: t.id,
    type: "remove"
  };
}
const Oh = (t) => ux(t), V1 = (t) => $p(t);
function dg(t) {
  return q.forwardRef(t);
}
function Fh(t) {
  const [r, i] = q.useState(BigInt(0)), [l] = q.useState(() => B1(() => i((u) => u + BigInt(1))));
  return ba(() => {
    const u = l.get();
    u.length && (t(u), l.reset());
  }, [r]), l;
}
function B1(t) {
  let r = [];
  return {
    get: () => r,
    reset: () => {
      r = [];
    },
    push: (i) => {
      r.push(i), t();
    }
  };
}
const hg = q.createContext(null);
function W1({ children: t }) {
  const r = je(), i = q.useCallback((h) => {
    const { nodes: p = [], setNodes: m, hasDefaultNodes: v, onNodesChange: g, nodeLookup: y, fitViewQueued: x, onNodesChangeMiddlewareMap: S } = r.getState();
    let M = p;
    for (const k of h)
      M = typeof k == "function" ? k(M) : k;
    let N = Dh({
      items: M,
      lookup: y
    });
    for (const k of S.values())
      N = k(N);
    v && m(M), N.length > 0 ? g == null || g(N) : x && window.requestAnimationFrame(() => {
      const { fitViewQueued: k, nodes: $, setNodes: _ } = r.getState();
      k && _($);
    });
  }, []), l = Fh(i), u = q.useCallback((h) => {
    const { edges: p = [], setEdges: m, hasDefaultEdges: v, onEdgesChange: g, edgeLookup: y } = r.getState();
    let x = p;
    for (const S of h)
      x = typeof S == "function" ? S(x) : S;
    v ? m(x) : g && g(Dh({
      items: x,
      lookup: y
    }));
  }, []), c = Fh(u), f = q.useMemo(() => ({ nodeQueue: l, edgeQueue: c }), []);
  return I.jsx(hg.Provider, { value: f, children: t });
}
function U1() {
  const t = q.useContext(hg);
  if (!t)
    throw new Error("useBatchContext must be used within a BatchProvider");
  return t;
}
const Y1 = (t) => !!t.panZoom;
function _l() {
  const t = j1(), r = je(), i = U1(), l = Me(Y1), u = q.useMemo(() => {
    const c = (g) => r.getState().nodeLookup.get(g), f = (g) => {
      i.nodeQueue.push(g);
    }, h = (g) => {
      i.edgeQueue.push(g);
    }, p = (g) => {
      var k, $;
      const { nodeLookup: y, nodeOrigin: x } = r.getState(), S = Oh(g) ? g : y.get(g.id), M = S.parentId ? Vp(S.position, S.measured, S.parentId, y, x) : S.position, N = {
        ...S,
        position: M,
        width: ((k = S.measured) == null ? void 0 : k.width) ?? S.width,
        height: (($ = S.measured) == null ? void 0 : $.height) ?? S.height
      };
      return Jr(N);
    }, m = (g, y, x = { replace: !1 }) => {
      f((S) => S.map((M) => {
        if (M.id === g) {
          const N = typeof y == "function" ? y(M) : y;
          return x.replace && Oh(N) ? N : { ...M, ...N };
        }
        return M;
      }));
    }, v = (g, y, x = { replace: !1 }) => {
      h((S) => S.map((M) => {
        if (M.id === g) {
          const N = typeof y == "function" ? y(M) : y;
          return x.replace && V1(N) ? N : { ...M, ...N };
        }
        return M;
      }));
    };
    return {
      getNodes: () => r.getState().nodes.map((g) => ({ ...g })),
      getNode: (g) => {
        var y;
        return (y = c(g)) == null ? void 0 : y.internals.userNode;
      },
      getInternalNode: c,
      getEdges: () => {
        const { edges: g = [] } = r.getState();
        return g.map((y) => ({ ...y }));
      },
      getEdge: (g) => r.getState().edgeLookup.get(g),
      setNodes: f,
      setEdges: h,
      addNodes: (g) => {
        const y = Array.isArray(g) ? g : [g];
        i.nodeQueue.push((x) => [...x, ...y]);
      },
      addEdges: (g) => {
        const y = Array.isArray(g) ? g : [g];
        i.edgeQueue.push((x) => [...x, ...y]);
      },
      toObject: () => {
        const { nodes: g = [], edges: y = [], transform: x } = r.getState(), [S, M, N] = x;
        return {
          nodes: g.map((k) => ({ ...k })),
          edges: y.map((k) => ({ ...k })),
          viewport: {
            x: S,
            y: M,
            zoom: N
          }
        };
      },
      deleteElements: async ({ nodes: g = [], edges: y = [] }) => {
        const { nodes: x, edges: S, onNodesDelete: M, onEdgesDelete: N, triggerNodeChanges: k, triggerEdgeChanges: $, onDelete: _, onBeforeDelete: E } = r.getState(), { nodes: D, edges: j } = await hx({
          nodesToRemove: g,
          edgesToRemove: y,
          nodes: x,
          edges: S,
          onBeforeDelete: E
        }), H = j.length > 0, b = D.length > 0;
        if (H) {
          const K = j.map(jh);
          N == null || N(j), $(K);
        }
        if (b) {
          const K = D.map(jh);
          M == null || M(D), k(K);
        }
        return (b || H) && (_ == null || _({ nodes: D, edges: j })), { deletedNodes: D, deletedEdges: j };
      },
      /**
       * Partial is defined as "the 2 nodes/areas are intersecting partially".
       * If a is contained in b or b is contained in a, they are both
       * considered fully intersecting.
       */
      getIntersectingNodes: (g, y = !0, x) => {
        const S = ch(g), M = S ? g : p(g), N = x !== void 0;
        return M ? (x || r.getState().nodes).filter((k) => {
          const $ = r.getState().nodeLookup.get(k.id);
          if ($ && !S && (k.id === g.id || !$.internals.positionAbsolute))
            return !1;
          const _ = Jr(N ? k : $), E = ui(_, M);
          return y && E > 0 || E >= _.width * _.height || E >= M.width * M.height;
        }) : [];
      },
      isNodeIntersecting: (g, y, x = !0) => {
        const M = ch(g) ? g : p(g);
        if (!M)
          return !1;
        const N = ui(M, y);
        return x && N > 0 || N >= y.width * y.height || N >= M.width * M.height;
      },
      updateNode: m,
      updateNodeData: (g, y, x = { replace: !1 }) => {
        m(g, (S) => {
          const M = typeof y == "function" ? y(S) : y;
          return x.replace ? { ...S, data: M } : { ...S, data: { ...S.data, ...M } };
        }, x);
      },
      updateEdge: v,
      updateEdgeData: (g, y, x = { replace: !1 }) => {
        v(g, (S) => {
          const M = typeof y == "function" ? y(S) : y;
          return x.replace ? { ...S, data: M } : { ...S, data: { ...S.data, ...M } };
        }, x);
      },
      getNodesBounds: (g) => {
        const { nodeLookup: y, nodeOrigin: x } = r.getState();
        return ax(g, { nodeLookup: y, nodeOrigin: x });
      },
      getHandleConnections: ({ type: g, id: y, nodeId: x }) => {
        var S;
        return Array.from(((S = r.getState().connectionLookup.get(`${x}-${g}${y ? `-${y}` : ""}`)) == null ? void 0 : S.values()) ?? []);
      },
      getNodeConnections: ({ type: g, handleId: y, nodeId: x }) => {
        var S;
        return Array.from(((S = r.getState().connectionLookup.get(`${x}${g ? y ? `-${g}-${y}` : `-${g}` : ""}`)) == null ? void 0 : S.values()) ?? []);
      },
      fitView: async (g) => {
        const y = r.getState().fitViewResolver ?? yx();
        return r.setState({ fitViewQueued: !0, fitViewOptions: g, fitViewResolver: y }), i.nodeQueue.push((x) => [...x]), y.promise;
      }
    };
  }, []);
  return q.useMemo(() => ({
    ...u,
    ...t,
    viewportInitialized: l
  }), [l]);
}
const Hh = (t) => t.selected, b1 = typeof window < "u" ? window : void 0;
function X1({ deleteKeyCode: t, multiSelectionKeyCode: r }) {
  const i = je(), { deleteElements: l } = _l(), u = ci(t, { actInsideInputWithModifier: !1 }), c = ci(r, { target: b1 });
  q.useEffect(() => {
    if (u) {
      const { edges: f, nodes: h } = i.getState();
      l({ nodes: h.filter(Hh), edges: f.filter(Hh) }), i.setState({ nodesSelectionActive: !1 });
    }
  }, [u]), q.useEffect(() => {
    i.setState({ multiSelectionActive: c });
  }, [c]);
}
function Q1(t) {
  const r = je();
  q.useEffect(() => {
    const i = () => {
      var u, c, f, h;
      if (!t.current || !(((c = (u = t.current).checkVisibility) == null ? void 0 : c.call(u)) ?? !0))
        return !1;
      const l = ic(t.current);
      (l.height === 0 || l.width === 0) && ((h = (f = r.getState()).onError) == null || h.call(f, "004", Kt.error004())), r.setState({ width: l.width || 500, height: l.height || 500 });
    };
    if (t.current) {
      i(), window.addEventListener("resize", i);
      const l = new ResizeObserver(() => i());
      return l.observe(t.current), () => {
        window.removeEventListener("resize", i), l && t.current && l.unobserve(t.current);
      };
    }
  }, []);
}
const El = {
  position: "absolute",
  width: "100%",
  height: "100%",
  top: 0,
  left: 0
}, K1 = (t) => ({
  userSelectionActive: t.userSelectionActive,
  lib: t.lib,
  connectionInProgress: t.connection.inProgress
});
function G1({ onPaneContextMenu: t, zoomOnScroll: r = !0, zoomOnPinch: i = !0, panOnScroll: l = !1, panOnScrollSpeed: u = 0.5, panOnScrollMode: c = ar.Free, zoomOnDoubleClick: f = !0, panOnDrag: h = !0, defaultViewport: p, translateExtent: m, minZoom: v, maxZoom: g, zoomActivationKeyCode: y, preventScrolling: x = !0, children: S, noWheelClassName: M, noPanClassName: N, onViewportChange: k, isControlledViewport: $, paneClickDistance: _, selectionOnDrag: E }) {
  const D = je(), j = q.useRef(null), { userSelectionActive: H, lib: b, connectionInProgress: K } = Me(K1, $e), ee = ci(y), U = q.useRef();
  Q1(j);
  const G = q.useCallback((Q) => {
    k == null || k({ x: Q[0], y: Q[1], zoom: Q[2] }), $ || D.setState({ transform: Q });
  }, [k, $]);
  return q.useEffect(() => {
    if (j.current) {
      U.current = e1({
        domNode: j.current,
        minZoom: v,
        maxZoom: g,
        translateExtent: m,
        viewport: p,
        onDraggingChange: (F) => D.setState((W) => W.paneDragging === F ? W : { paneDragging: F }),
        onPanZoomStart: (F, W) => {
          const { onViewportChangeStart: T, onMoveStart: L } = D.getState();
          L == null || L(F, W), T == null || T(W);
        },
        onPanZoom: (F, W) => {
          const { onViewportChange: T, onMove: L } = D.getState();
          L == null || L(F, W), T == null || T(W);
        },
        onPanZoomEnd: (F, W) => {
          const { onViewportChangeEnd: T, onMoveEnd: L } = D.getState();
          L == null || L(F, W), T == null || T(W);
        }
      });
      const { x: Q, y: z, zoom: B } = U.current.getViewport();
      return D.setState({
        panZoom: U.current,
        transform: [Q, z, B],
        domNode: j.current.closest(".react-flow")
      }), () => {
        var F;
        (F = U.current) == null || F.destroy();
      };
    }
  }, []), q.useEffect(() => {
    var Q;
    (Q = U.current) == null || Q.update({
      onPaneContextMenu: t,
      zoomOnScroll: r,
      zoomOnPinch: i,
      panOnScroll: l,
      panOnScrollSpeed: u,
      panOnScrollMode: c,
      zoomOnDoubleClick: f,
      panOnDrag: h,
      zoomActivationKeyPressed: ee,
      preventScrolling: x,
      noPanClassName: N,
      userSelectionActive: H,
      noWheelClassName: M,
      lib: b,
      onTransformChange: G,
      connectionInProgress: K,
      selectionOnDrag: E,
      paneClickDistance: _
    });
  }, [
    t,
    r,
    i,
    l,
    u,
    c,
    f,
    h,
    ee,
    x,
    N,
    H,
    M,
    b,
    G,
    K,
    E,
    _
  ]), I.jsx("div", { className: "react-flow__renderer", ref: j, style: El, children: S });
}
const Z1 = (t) => ({
  userSelectionActive: t.userSelectionActive,
  userSelectionRect: t.userSelectionRect
});
function q1() {
  const { userSelectionActive: t, userSelectionRect: r } = Me(Z1, $e);
  return t && r ? I.jsx("div", { className: "react-flow__selection react-flow__container", style: {
    width: r.width,
    height: r.height,
    transform: `translate(${r.x}px, ${r.y}px)`
  } }) : null;
}
const La = (t, r) => (i) => {
  i.target === r.current && (t == null || t(i));
}, J1 = (t) => ({
  userSelectionActive: t.userSelectionActive,
  elementsSelectable: t.elementsSelectable,
  connectionInProgress: t.connection.inProgress,
  dragging: t.paneDragging
});
function eS({ isSelecting: t, selectionKeyPressed: r, selectionMode: i = si.Full, panOnDrag: l, paneClickDistance: u, selectionOnDrag: c, onSelectionStart: f, onSelectionEnd: h, onPaneClick: p, onPaneContextMenu: m, onPaneScroll: v, onPaneMouseEnter: g, onPaneMouseMove: y, onPaneMouseLeave: x, children: S }) {
  const M = je(), { userSelectionActive: N, elementsSelectable: k, dragging: $, connectionInProgress: _ } = Me(J1, $e), E = k && (t || N), D = q.useRef(null), j = q.useRef(), H = q.useRef(/* @__PURE__ */ new Set()), b = q.useRef(/* @__PURE__ */ new Set()), K = q.useRef(!1), ee = (T) => {
    if (K.current || _) {
      K.current = !1;
      return;
    }
    p == null || p(T), M.getState().resetSelectedElements(), M.setState({ nodesSelectionActive: !1 });
  }, U = (T) => {
    if (Array.isArray(l) && (l != null && l.includes(2))) {
      T.preventDefault();
      return;
    }
    m == null || m(T);
  }, G = v ? (T) => v(T) : void 0, Q = (T) => {
    K.current && (T.stopPropagation(), K.current = !1);
  }, z = (T) => {
    var le, ue;
    const { domNode: L } = M.getState();
    if (j.current = L == null ? void 0 : L.getBoundingClientRect(), !j.current)
      return;
    const V = T.target === D.current;
    if (!V && !!T.target.closest(".nokey") || !t || !(c && V || r) || T.button !== 0 || !T.isPrimary)
      return;
    (ue = (le = T.target) == null ? void 0 : le.setPointerCapture) == null || ue.call(le, T.pointerId), K.current = !1;
    const { x: te, y: re } = Ft(T.nativeEvent, j.current);
    M.setState({
      userSelectionRect: {
        width: 0,
        height: 0,
        startX: te,
        startY: re,
        x: te,
        y: re
      }
    }), V || (T.stopPropagation(), T.preventDefault());
  }, B = (T) => {
    const { userSelectionRect: L, transform: V, nodeLookup: C, edgeLookup: A, connectionLookup: te, triggerNodeChanges: re, triggerEdgeChanges: le, defaultEdgeOptions: ue, resetSelectedElements: ae } = M.getState();
    if (!j.current || !L)
      return;
    const { x: J, y: ce } = Ft(T.nativeEvent, j.current), { startX: xe, startY: _e } = L;
    if (!K.current) {
      const Ce = r ? 0 : u;
      if (Math.hypot(J - xe, ce - _e) <= Ce)
        return;
      ae(), f == null || f(T);
    }
    K.current = !0;
    const Se = {
      startX: xe,
      startY: _e,
      x: J < xe ? J : xe,
      y: ce < _e ? ce : _e,
      width: Math.abs(J - xe),
      height: Math.abs(ce - _e)
    }, ye = H.current, Ne = b.current;
    H.current = new Set(rc(C, Se, V, i === si.Partial, !0).map((Ce) => Ce.id)), b.current = /* @__PURE__ */ new Set();
    const Te = (ue == null ? void 0 : ue.selectable) ?? !0;
    for (const Ce of H.current) {
      const We = te.get(Ce);
      if (We)
        for (const { edgeId: It } of We.values()) {
          const dt = A.get(It);
          dt && (dt.selectable ?? Te) && b.current.add(It);
        }
    }
    if (!fh(ye, H.current)) {
      const Ce = br(C, H.current, !0);
      re(Ce);
    }
    if (!fh(Ne, b.current)) {
      const Ce = br(A, b.current);
      le(Ce);
    }
    M.setState({
      userSelectionRect: Se,
      userSelectionActive: !0,
      nodesSelectionActive: !1
    });
  }, F = (T) => {
    var L, V;
    T.button === 0 && ((V = (L = T.target) == null ? void 0 : L.releasePointerCapture) == null || V.call(L, T.pointerId), !N && T.target === D.current && M.getState().userSelectionRect && (ee == null || ee(T)), M.setState({
      userSelectionActive: !1,
      userSelectionRect: null
    }), K.current && (h == null || h(T), M.setState({
      nodesSelectionActive: H.current.size > 0
    })));
  }, W = l === !0 || Array.isArray(l) && l.includes(0);
  return I.jsxs("div", { className: Ye(["react-flow__pane", { draggable: W, dragging: $, selection: t }]), onClick: E ? void 0 : La(ee, D), onContextMenu: La(U, D), onWheel: La(G, D), onPointerEnter: E ? void 0 : g, onPointerMove: E ? B : y, onPointerUp: E ? F : void 0, onPointerDownCapture: E ? z : void 0, onClickCapture: E ? Q : void 0, onPointerLeave: x, ref: D, style: El, children: [S, I.jsx(q1, {})] });
}
function Xa({ id: t, store: r, unselect: i = !1, nodeRef: l }) {
  const { addSelectedNodes: u, unselectNodesAndEdges: c, multiSelectionActive: f, nodeLookup: h, onError: p } = r.getState(), m = h.get(t);
  if (!m) {
    p == null || p("012", Kt.error012(t));
    return;
  }
  r.setState({ nodesSelectionActive: !1 }), m.selected ? (i || m.selected && f) && (c({ nodes: [m], edges: [] }), requestAnimationFrame(() => {
    var v;
    return (v = l == null ? void 0 : l.current) == null ? void 0 : v.blur();
  })) : u([t]);
}
function pg({ nodeRef: t, disabled: r = !1, noDragClassName: i, handleSelector: l, nodeId: u, isSelectable: c, nodeClickDistance: f }) {
  const h = je(), [p, m] = q.useState(!1), v = q.useRef();
  return q.useEffect(() => {
    v.current = Hx({
      getStoreItems: () => h.getState(),
      onNodeMouseDown: (g) => {
        Xa({
          id: g,
          store: h,
          nodeRef: t
        });
      },
      onDragStart: () => {
        m(!0);
      },
      onDragStop: () => {
        m(!1);
      }
    });
  }, []), q.useEffect(() => {
    if (!(r || !t.current || !v.current))
      return v.current.update({
        noDragClassName: i,
        handleSelector: l,
        domNode: t.current,
        isSelectable: c,
        nodeId: u,
        nodeClickDistance: f
      }), () => {
        var g;
        (g = v.current) == null || g.destroy();
      };
  }, [i, l, r, c, t, u, f]), p;
}
const tS = (t) => (r) => r.selected && (r.draggable || t && typeof r.draggable > "u");
function gg() {
  const t = je();
  return q.useCallback((i) => {
    const { nodeExtent: l, snapToGrid: u, snapGrid: c, nodesDraggable: f, onError: h, updateNodePositions: p, nodeLookup: m, nodeOrigin: v } = t.getState(), g = /* @__PURE__ */ new Map(), y = tS(f), x = u ? c[0] : 5, S = u ? c[1] : 5, M = i.direction.x * x * i.factor, N = i.direction.y * S * i.factor;
    for (const [, k] of m) {
      if (!y(k))
        continue;
      let $ = {
        x: k.internals.positionAbsolute.x + M,
        y: k.internals.positionAbsolute.y + N
      };
      u && ($ = mi($, c));
      const { position: _, positionAbsolute: E } = Dp({
        nodeId: k.id,
        nextPosition: $,
        nodeLookup: m,
        nodeExtent: l,
        nodeOrigin: v,
        onError: h
      });
      k.position = _, k.internals.positionAbsolute = E, g.set(k.id, k);
    }
    p(g);
  }, []);
}
const fc = q.createContext(null), nS = fc.Provider;
fc.Consumer;
const mg = () => q.useContext(fc), rS = (t) => ({
  connectOnClick: t.connectOnClick,
  noPanClassName: t.noPanClassName,
  rfId: t.rfId
}), oS = (t, r, i) => (l) => {
  const { connectionClickStartHandle: u, connectionMode: c, connection: f } = l, { fromHandle: h, toHandle: p, isValid: m } = f, v = (p == null ? void 0 : p.nodeId) === t && (p == null ? void 0 : p.id) === r && (p == null ? void 0 : p.type) === i;
  return {
    connectingFrom: (h == null ? void 0 : h.nodeId) === t && (h == null ? void 0 : h.id) === r && (h == null ? void 0 : h.type) === i,
    connectingTo: v,
    clickConnecting: (u == null ? void 0 : u.nodeId) === t && (u == null ? void 0 : u.id) === r && (u == null ? void 0 : u.type) === i,
    isPossibleEndHandle: c === Zr.Strict ? (h == null ? void 0 : h.type) !== i : t !== (h == null ? void 0 : h.nodeId) || r !== (h == null ? void 0 : h.id),
    connectionInProcess: !!h,
    clickConnectionInProcess: !!u,
    valid: v && m
  };
};
function iS({ type: t = "source", position: r = me.Top, isValidConnection: i, isConnectable: l = !0, isConnectableStart: u = !0, isConnectableEnd: c = !0, id: f, onConnect: h, children: p, className: m, onMouseDown: v, onTouchStart: g, ...y }, x) {
  var B, F;
  const S = f || null, M = t === "target", N = je(), k = mg(), { connectOnClick: $, noPanClassName: _, rfId: E } = Me(rS, $e), { connectingFrom: D, connectingTo: j, clickConnecting: H, isPossibleEndHandle: b, connectionInProcess: K, clickConnectionInProcess: ee, valid: U } = Me(oS(k, S, t), $e);
  k || (F = (B = N.getState()).onError) == null || F.call(B, "010", Kt.error010());
  const G = (W) => {
    const { defaultEdgeOptions: T, onConnect: L, hasDefaultEdges: V } = N.getState(), C = {
      ...T,
      ...W
    };
    if (V) {
      const { edges: A, setEdges: te } = N.getState();
      te(kx(C, A));
    }
    L == null || L(C), h == null || h(C);
  }, Q = (W) => {
    if (!k)
      return;
    const T = Up(W.nativeEvent);
    if (u && (T && W.button === 0 || !T)) {
      const L = N.getState();
      Ya.onPointerDown(W.nativeEvent, {
        handleDomNode: W.currentTarget,
        autoPanOnConnect: L.autoPanOnConnect,
        connectionMode: L.connectionMode,
        connectionRadius: L.connectionRadius,
        domNode: L.domNode,
        nodeLookup: L.nodeLookup,
        lib: L.lib,
        isTarget: M,
        handleId: S,
        nodeId: k,
        flowId: L.rfId,
        panBy: L.panBy,
        cancelConnection: L.cancelConnection,
        onConnectStart: L.onConnectStart,
        onConnectEnd: (...V) => {
          var C, A;
          return (A = (C = N.getState()).onConnectEnd) == null ? void 0 : A.call(C, ...V);
        },
        updateConnection: L.updateConnection,
        onConnect: G,
        isValidConnection: i || ((...V) => {
          var C, A;
          return ((A = (C = N.getState()).isValidConnection) == null ? void 0 : A.call(C, ...V)) ?? !0;
        }),
        getTransform: () => N.getState().transform,
        getFromHandle: () => N.getState().connection.fromHandle,
        autoPanSpeed: L.autoPanSpeed,
        dragThreshold: L.connectionDragThreshold
      });
    }
    T ? v == null || v(W) : g == null || g(W);
  }, z = (W) => {
    const { onClickConnectStart: T, onClickConnectEnd: L, connectionClickStartHandle: V, connectionMode: C, isValidConnection: A, lib: te, rfId: re, nodeLookup: le, connection: ue } = N.getState();
    if (!k || !V && !u)
      return;
    if (!V) {
      T == null || T(W.nativeEvent, { nodeId: k, handleId: S, handleType: t }), N.setState({ connectionClickStartHandle: { nodeId: k, type: t, id: S } });
      return;
    }
    const ae = Bp(W.target), J = i || A, { connection: ce, isValid: xe } = Ya.isValid(W.nativeEvent, {
      handle: {
        nodeId: k,
        id: S,
        type: t
      },
      connectionMode: C,
      fromNodeId: V.nodeId,
      fromHandleId: V.id || null,
      fromType: V.type,
      isValidConnection: J,
      flowId: re,
      doc: ae,
      lib: te,
      nodeLookup: le
    });
    xe && ce && G(ce);
    const _e = structuredClone(ue);
    delete _e.inProgress, _e.toPosition = _e.toHandle ? _e.toHandle.position : null, L == null || L(W, _e), N.setState({ connectionClickStartHandle: null });
  };
  return I.jsx("div", { "data-handleid": S, "data-nodeid": k, "data-handlepos": r, "data-id": `${E}-${k}-${S}-${t}`, className: Ye([
    "react-flow__handle",
    `react-flow__handle-${r}`,
    "nodrag",
    _,
    m,
    {
      source: !M,
      target: M,
      connectable: l,
      connectablestart: u,
      connectableend: c,
      clickconnecting: H,
      connectingfrom: D,
      connectingto: j,
      valid: U,
      /*
       * shows where you can start a connection from
       * and where you can end it while connecting
       */
      connectionindicator: l && (!K || b) && (K || ee ? c : u)
    }
  ]), onMouseDown: Q, onTouchStart: Q, onClick: $ ? z : void 0, ref: x, ...y, children: p });
}
const no = q.memo(dg(iS));
function sS({ data: t, isConnectable: r, sourcePosition: i = me.Bottom }) {
  return I.jsxs(I.Fragment, { children: [t == null ? void 0 : t.label, I.jsx(no, { type: "source", position: i, isConnectable: r })] });
}
function lS({ data: t, isConnectable: r, targetPosition: i = me.Top, sourcePosition: l = me.Bottom }) {
  return I.jsxs(I.Fragment, { children: [I.jsx(no, { type: "target", position: i, isConnectable: r }), t == null ? void 0 : t.label, I.jsx(no, { type: "source", position: l, isConnectable: r })] });
}
function uS() {
  return null;
}
function aS({ data: t, isConnectable: r, targetPosition: i = me.Top }) {
  return I.jsxs(I.Fragment, { children: [I.jsx(no, { type: "target", position: i, isConnectable: r }), t == null ? void 0 : t.label] });
}
const al = {
  ArrowUp: { x: 0, y: -1 },
  ArrowDown: { x: 0, y: 1 },
  ArrowLeft: { x: -1, y: 0 },
  ArrowRight: { x: 1, y: 0 }
}, Vh = {
  input: sS,
  default: lS,
  output: aS,
  group: uS
};
function cS(t) {
  var r, i, l, u;
  return t.internals.handleBounds === void 0 ? {
    width: t.width ?? t.initialWidth ?? ((r = t.style) == null ? void 0 : r.width),
    height: t.height ?? t.initialHeight ?? ((i = t.style) == null ? void 0 : i.height)
  } : {
    width: t.width ?? ((l = t.style) == null ? void 0 : l.width),
    height: t.height ?? ((u = t.style) == null ? void 0 : u.height)
  };
}
const fS = (t) => {
  const { width: r, height: i, x: l, y: u } = gi(t.nodeLookup, {
    filter: (c) => !!c.selected
  });
  return {
    width: Ot(r) ? r : null,
    height: Ot(i) ? i : null,
    userSelectionActive: t.userSelectionActive,
    transformString: `translate(${t.transform[0]}px,${t.transform[1]}px) scale(${t.transform[2]}) translate(${l}px,${u}px)`
  };
};
function dS({ onSelectionContextMenu: t, noPanClassName: r, disableKeyboardA11y: i }) {
  const l = je(), { width: u, height: c, transformString: f, userSelectionActive: h } = Me(fS, $e), p = gg(), m = q.useRef(null);
  q.useEffect(() => {
    var x;
    i || (x = m.current) == null || x.focus({
      preventScroll: !0
    });
  }, [i]);
  const v = !h && u !== null && c !== null;
  if (pg({
    nodeRef: m,
    disabled: !v
  }), !v)
    return null;
  const g = t ? (x) => {
    const S = l.getState().nodes.filter((M) => M.selected);
    t(x, S);
  } : void 0, y = (x) => {
    Object.prototype.hasOwnProperty.call(al, x.key) && (x.preventDefault(), p({
      direction: al[x.key],
      factor: x.shiftKey ? 4 : 1
    }));
  };
  return I.jsx("div", { className: Ye(["react-flow__nodesselection", "react-flow__container", r]), style: {
    transform: f
  }, children: I.jsx("div", { ref: m, className: "react-flow__nodesselection-rect", onContextMenu: g, tabIndex: i ? void 0 : -1, onKeyDown: i ? void 0 : y, style: {
    width: u,
    height: c
  } }) });
}
const Bh = typeof window < "u" ? window : void 0, hS = (t) => ({ nodesSelectionActive: t.nodesSelectionActive, userSelectionActive: t.userSelectionActive });
function yg({ children: t, onPaneClick: r, onPaneMouseEnter: i, onPaneMouseMove: l, onPaneMouseLeave: u, onPaneContextMenu: c, onPaneScroll: f, paneClickDistance: h, deleteKeyCode: p, selectionKeyCode: m, selectionOnDrag: v, selectionMode: g, onSelectionStart: y, onSelectionEnd: x, multiSelectionKeyCode: S, panActivationKeyCode: M, zoomActivationKeyCode: N, elementsSelectable: k, zoomOnScroll: $, zoomOnPinch: _, panOnScroll: E, panOnScrollSpeed: D, panOnScrollMode: j, zoomOnDoubleClick: H, panOnDrag: b, defaultViewport: K, translateExtent: ee, minZoom: U, maxZoom: G, preventScrolling: Q, onSelectionContextMenu: z, noWheelClassName: B, noPanClassName: F, disableKeyboardA11y: W, onViewportChange: T, isControlledViewport: L }) {
  const { nodesSelectionActive: V, userSelectionActive: C } = Me(hS, $e), A = ci(m, { target: Bh }), te = ci(M, { target: Bh }), re = te || b, le = te || E, ue = v && re !== !0, ae = A || C || ue;
  return X1({ deleteKeyCode: p, multiSelectionKeyCode: S }), I.jsx(G1, { onPaneContextMenu: c, elementsSelectable: k, zoomOnScroll: $, zoomOnPinch: _, panOnScroll: le, panOnScrollSpeed: D, panOnScrollMode: j, zoomOnDoubleClick: H, panOnDrag: !A && re, defaultViewport: K, translateExtent: ee, minZoom: U, maxZoom: G, zoomActivationKeyCode: N, preventScrolling: Q, noWheelClassName: B, noPanClassName: F, onViewportChange: T, isControlledViewport: L, paneClickDistance: h, selectionOnDrag: ue, children: I.jsxs(eS, { onSelectionStart: y, onSelectionEnd: x, onPaneClick: r, onPaneMouseEnter: i, onPaneMouseMove: l, onPaneMouseLeave: u, onPaneContextMenu: c, onPaneScroll: f, panOnDrag: re, isSelecting: !!ae, selectionMode: g, selectionKeyPressed: A, paneClickDistance: h, selectionOnDrag: ue, children: [t, V && I.jsx(dS, { onSelectionContextMenu: z, noPanClassName: F, disableKeyboardA11y: W })] }) });
}
yg.displayName = "FlowRenderer";
const pS = q.memo(yg), gS = (t) => (r) => t ? rc(r.nodeLookup, { x: 0, y: 0, width: r.width, height: r.height }, r.transform, !0).map((i) => i.id) : Array.from(r.nodeLookup.keys());
function mS(t) {
  return Me(q.useCallback(gS(t), [t]), $e);
}
const yS = (t) => t.updateNodeInternals;
function vS() {
  const t = Me(yS), [r] = q.useState(() => typeof ResizeObserver > "u" ? null : new ResizeObserver((i) => {
    const l = /* @__PURE__ */ new Map();
    i.forEach((u) => {
      const c = u.target.getAttribute("data-id");
      l.set(c, {
        id: c,
        nodeElement: u.target,
        force: !0
      });
    }), t(l);
  }));
  return q.useEffect(() => () => {
    r == null || r.disconnect();
  }, [r]), r;
}
function wS({ node: t, nodeType: r, hasDimensions: i, resizeObserver: l }) {
  const u = je(), c = q.useRef(null), f = q.useRef(null), h = q.useRef(t.sourcePosition), p = q.useRef(t.targetPosition), m = q.useRef(r), v = i && !!t.internals.handleBounds;
  return q.useEffect(() => {
    c.current && !t.hidden && (!v || f.current !== c.current) && (f.current && (l == null || l.unobserve(f.current)), l == null || l.observe(c.current), f.current = c.current);
  }, [v, t.hidden]), q.useEffect(() => () => {
    f.current && (l == null || l.unobserve(f.current), f.current = null);
  }, []), q.useEffect(() => {
    if (c.current) {
      const g = m.current !== r, y = h.current !== t.sourcePosition, x = p.current !== t.targetPosition;
      (g || y || x) && (m.current = r, h.current = t.sourcePosition, p.current = t.targetPosition, u.getState().updateNodeInternals(/* @__PURE__ */ new Map([[t.id, { id: t.id, nodeElement: c.current, force: !0 }]])));
    }
  }, [t.id, r, t.sourcePosition, t.targetPosition]), c;
}
function xS({ id: t, onClick: r, onMouseEnter: i, onMouseMove: l, onMouseLeave: u, onContextMenu: c, onDoubleClick: f, nodesDraggable: h, elementsSelectable: p, nodesConnectable: m, nodesFocusable: v, resizeObserver: g, noDragClassName: y, noPanClassName: x, disableKeyboardA11y: S, rfId: M, nodeTypes: N, nodeClickDistance: k, onError: $ }) {
  const { node: _, internals: E, isParent: D } = Me((J) => {
    const ce = J.nodeLookup.get(t), xe = J.parentLookup.has(t);
    return {
      node: ce,
      internals: ce.internals,
      isParent: xe
    };
  }, $e);
  let j = _.type || "default", H = (N == null ? void 0 : N[j]) || Vh[j];
  H === void 0 && ($ == null || $("003", Kt.error003(j)), j = "default", H = (N == null ? void 0 : N.default) || Vh.default);
  const b = !!(_.draggable || h && typeof _.draggable > "u"), K = !!(_.selectable || p && typeof _.selectable > "u"), ee = !!(_.connectable || m && typeof _.connectable > "u"), U = !!(_.focusable || v && typeof _.focusable > "u"), G = je(), Q = Hp(_), z = wS({ node: _, nodeType: j, hasDimensions: Q, resizeObserver: g }), B = pg({
    nodeRef: z,
    disabled: _.hidden || !b,
    noDragClassName: y,
    handleSelector: _.dragHandle,
    nodeId: t,
    isSelectable: K,
    nodeClickDistance: k
  }), F = gg();
  if (_.hidden)
    return null;
  const W = hn(_), T = cS(_), L = K || b || r || i || l || u, V = i ? (J) => i(J, { ...E.userNode }) : void 0, C = l ? (J) => l(J, { ...E.userNode }) : void 0, A = u ? (J) => u(J, { ...E.userNode }) : void 0, te = c ? (J) => c(J, { ...E.userNode }) : void 0, re = f ? (J) => f(J, { ...E.userNode }) : void 0, le = (J) => {
    const { selectNodesOnDrag: ce, nodeDragThreshold: xe } = G.getState();
    K && (!ce || !b || xe > 0) && Xa({
      id: t,
      store: G,
      nodeRef: z
    }), r && r(J, { ...E.userNode });
  }, ue = (J) => {
    if (!(Wp(J.nativeEvent) || S)) {
      if (Tp.includes(J.key) && K) {
        const ce = J.key === "Escape";
        Xa({
          id: t,
          store: G,
          unselect: ce,
          nodeRef: z
        });
      } else if (b && _.selected && Object.prototype.hasOwnProperty.call(al, J.key)) {
        J.preventDefault();
        const { ariaLabelConfig: ce } = G.getState();
        G.setState({
          ariaLiveMessage: ce["node.a11yDescription.ariaLiveMessage"]({
            direction: J.key.replace("Arrow", "").toLowerCase(),
            x: ~~E.positionAbsolute.x,
            y: ~~E.positionAbsolute.y
          })
        }), F({
          direction: al[J.key],
          factor: J.shiftKey ? 4 : 1
        });
      }
    }
  }, ae = () => {
    var Ne;
    if (S || !((Ne = z.current) != null && Ne.matches(":focus-visible")))
      return;
    const { transform: J, width: ce, height: xe, autoPanOnNodeFocus: _e, setCenter: Se } = G.getState();
    if (!_e)
      return;
    rc(/* @__PURE__ */ new Map([[t, _]]), { x: 0, y: 0, width: ce, height: xe }, J, !0).length > 0 || Se(_.position.x + W.width / 2, _.position.y + W.height / 2, {
      zoom: J[2]
    });
  };
  return I.jsx("div", { className: Ye([
    "react-flow__node",
    `react-flow__node-${j}`,
    {
      // this is overwritable by passing `nopan` as a class name
      [x]: b
    },
    _.className,
    {
      selected: _.selected,
      selectable: K,
      parent: D,
      draggable: b,
      dragging: B
    }
  ]), ref: z, style: {
    zIndex: E.z,
    transform: `translate(${E.positionAbsolute.x}px,${E.positionAbsolute.y}px)`,
    pointerEvents: L ? "all" : "none",
    visibility: Q ? "visible" : "hidden",
    ..._.style,
    ...T
  }, "data-id": t, "data-testid": `rf__node-${t}`, onMouseEnter: V, onMouseMove: C, onMouseLeave: A, onContextMenu: te, onClick: le, onDoubleClick: re, onKeyDown: U ? ue : void 0, tabIndex: U ? 0 : void 0, onFocus: U ? ae : void 0, role: _.ariaRole ?? (U ? "group" : void 0), "aria-roledescription": "node", "aria-describedby": S ? void 0 : `${ug}-${M}`, "aria-label": _.ariaLabel, ..._.domAttributes, children: I.jsx(nS, { value: t, children: I.jsx(H, { id: t, data: _.data, type: j, positionAbsoluteX: E.positionAbsolute.x, positionAbsoluteY: E.positionAbsolute.y, selected: _.selected ?? !1, selectable: K, draggable: b, deletable: _.deletable ?? !0, isConnectable: ee, sourcePosition: _.sourcePosition, targetPosition: _.targetPosition, dragging: B, dragHandle: _.dragHandle, zIndex: E.z, parentId: _.parentId, ...W }) }) });
}
var SS = q.memo(xS);
const _S = (t) => ({
  nodesDraggable: t.nodesDraggable,
  nodesConnectable: t.nodesConnectable,
  nodesFocusable: t.nodesFocusable,
  elementsSelectable: t.elementsSelectable,
  onError: t.onError
});
function vg(t) {
  const { nodesDraggable: r, nodesConnectable: i, nodesFocusable: l, elementsSelectable: u, onError: c } = Me(_S, $e), f = mS(t.onlyRenderVisibleElements), h = vS();
  return I.jsx("div", { className: "react-flow__nodes", style: El, children: f.map((p) => (
    /*
     * The split of responsibilities between NodeRenderer and
     * NodeComponentWrapper may appear weird. However, it’s designed to
     * minimize the cost of updates when individual nodes change.
     *
     * For example, when you’re dragging a single node, that node gets
     * updated multiple times per second. If `NodeRenderer` were to update
     * every time, it would have to re-run the `nodes.map()` loop every
     * time. This gets pricey with hundreds of nodes, especially if every
     * loop cycle does more than just rendering a JSX element!
     *
     * As a result of this choice, we took the following implementation
     * decisions:
     * - NodeRenderer subscribes *only* to node IDs – and therefore
     *   rerender *only* when visible nodes are added or removed.
     * - NodeRenderer performs all operations the result of which can be
     *   shared between nodes (such as creating the `ResizeObserver`
     *   instance, or subscribing to `selector`). This means extra prop
     *   drilling into `NodeComponentWrapper`, but it means we need to run
     *   these operations only once – instead of once per node.
     * - Any operations that you’d normally write inside `nodes.map` are
     *   moved into `NodeComponentWrapper`. This ensures they are
     *   memorized – so if `NodeRenderer` *has* to rerender, it only
     *   needs to regenerate the list of nodes, nothing else.
     */
    I.jsx(SS, { id: p, nodeTypes: t.nodeTypes, nodeExtent: t.nodeExtent, onClick: t.onNodeClick, onMouseEnter: t.onNodeMouseEnter, onMouseMove: t.onNodeMouseMove, onMouseLeave: t.onNodeMouseLeave, onContextMenu: t.onNodeContextMenu, onDoubleClick: t.onNodeDoubleClick, noDragClassName: t.noDragClassName, noPanClassName: t.noPanClassName, rfId: t.rfId, disableKeyboardA11y: t.disableKeyboardA11y, resizeObserver: h, nodesDraggable: r, nodesConnectable: i, nodesFocusable: l, elementsSelectable: u, nodeClickDistance: t.nodeClickDistance, onError: c }, p)
  )) });
}
vg.displayName = "NodeRenderer";
const ES = q.memo(vg);
function kS(t) {
  return Me(q.useCallback((i) => {
    if (!t)
      return i.edges.map((u) => u.id);
    const l = [];
    if (i.width && i.height)
      for (const u of i.edges) {
        const c = i.nodeLookup.get(u.source), f = i.nodeLookup.get(u.target);
        c && f && Sx({
          sourceNode: c,
          targetNode: f,
          width: i.width,
          height: i.height,
          transform: i.transform
        }) && l.push(u.id);
      }
    return l;
  }, [t]), $e);
}
const NS = ({ color: t = "none", strokeWidth: r = 1 }) => {
  const i = {
    strokeWidth: r,
    ...t && { stroke: t }
  };
  return I.jsx("polyline", { className: "arrow", style: i, strokeLinecap: "round", fill: "none", strokeLinejoin: "round", points: "-5,-4 0,0 -5,4" });
}, CS = ({ color: t = "none", strokeWidth: r = 1 }) => {
  const i = {
    strokeWidth: r,
    ...t && { stroke: t, fill: t }
  };
  return I.jsx("polyline", { className: "arrowclosed", style: i, strokeLinecap: "round", strokeLinejoin: "round", points: "-5,-4 0,0 -5,4 -5,-4" });
}, Wh = {
  [li.Arrow]: NS,
  [li.ArrowClosed]: CS
};
function MS(t) {
  const r = je();
  return q.useMemo(() => {
    var u, c;
    return Object.prototype.hasOwnProperty.call(Wh, t) ? Wh[t] : ((c = (u = r.getState()).onError) == null || c.call(u, "009", Kt.error009(t)), null);
  }, [t]);
}
const PS = ({ id: t, type: r, color: i, width: l = 12.5, height: u = 12.5, markerUnits: c = "strokeWidth", strokeWidth: f, orient: h = "auto-start-reverse" }) => {
  const p = MS(r);
  return p ? I.jsx("marker", { className: "react-flow__arrowhead", id: t, markerWidth: `${l}`, markerHeight: `${u}`, viewBox: "-10 -10 20 20", markerUnits: c, orient: h, refX: "0", refY: "0", children: I.jsx(p, { color: i, strokeWidth: f }) }) : null;
}, wg = ({ defaultColor: t, rfId: r }) => {
  const i = Me((c) => c.edges), l = Me((c) => c.defaultEdgeOptions), u = q.useMemo(() => Ix(i, {
    id: r,
    defaultColor: t,
    defaultMarkerStart: l == null ? void 0 : l.markerStart,
    defaultMarkerEnd: l == null ? void 0 : l.markerEnd
  }), [i, l, r, t]);
  return u.length ? I.jsx("svg", { className: "react-flow__marker", "aria-hidden": "true", children: I.jsx("defs", { children: u.map((c) => I.jsx(PS, { id: c.id, type: c.type, color: c.color, width: c.width, height: c.height, markerUnits: c.markerUnits, strokeWidth: c.strokeWidth, orient: c.orient }, c.id)) }) }) : null;
};
wg.displayName = "MarkerDefinitions";
var IS = q.memo(wg);
function xg({ x: t, y: r, label: i, labelStyle: l, labelShowBg: u = !0, labelBgStyle: c, labelBgPadding: f = [2, 4], labelBgBorderRadius: h = 2, children: p, className: m, ...v }) {
  const [g, y] = q.useState({ x: 1, y: 0, width: 0, height: 0 }), x = Ye(["react-flow__edge-textwrapper", m]), S = q.useRef(null);
  return q.useEffect(() => {
    if (S.current) {
      const M = S.current.getBBox();
      y({
        x: M.x,
        y: M.y,
        width: M.width,
        height: M.height
      });
    }
  }, [i]), i ? I.jsxs("g", { transform: `translate(${t - g.width / 2} ${r - g.height / 2})`, className: x, visibility: g.width ? "visible" : "hidden", ...v, children: [u && I.jsx("rect", { width: g.width + 2 * f[0], x: -f[0], y: -f[1], height: g.height + 2 * f[1], className: "react-flow__edge-textbg", style: c, rx: h, ry: h }), I.jsx("text", { className: "react-flow__edge-text", y: g.height / 2, dy: "0.3em", ref: S, style: l, children: i }), p] }) : null;
}
xg.displayName = "EdgeText";
const zS = q.memo(xg);
function vi({ path: t, labelX: r, labelY: i, label: l, labelStyle: u, labelShowBg: c, labelBgStyle: f, labelBgPadding: h, labelBgBorderRadius: p, interactionWidth: m = 20, ...v }) {
  return I.jsxs(I.Fragment, { children: [I.jsx("path", { ...v, d: t, fill: "none", className: Ye(["react-flow__edge-path", v.className]) }), m ? I.jsx("path", { d: t, fill: "none", strokeOpacity: 0, strokeWidth: m, className: "react-flow__edge-interaction" }) : null, l && Ot(r) && Ot(i) ? I.jsx(zS, { x: r, y: i, label: l, labelStyle: u, labelShowBg: c, labelBgStyle: f, labelBgPadding: h, labelBgBorderRadius: p }) : null] });
}
function Uh({ pos: t, x1: r, y1: i, x2: l, y2: u }) {
  return t === me.Left || t === me.Right ? [0.5 * (r + l), i] : [r, 0.5 * (i + u)];
}
function Sg({ sourceX: t, sourceY: r, sourcePosition: i = me.Bottom, targetX: l, targetY: u, targetPosition: c = me.Top }) {
  const [f, h] = Uh({
    pos: i,
    x1: t,
    y1: r,
    x2: l,
    y2: u
  }), [p, m] = Uh({
    pos: c,
    x1: l,
    y1: u,
    x2: t,
    y2: r
  }), [v, g, y, x] = Yp({
    sourceX: t,
    sourceY: r,
    targetX: l,
    targetY: u,
    sourceControlX: f,
    sourceControlY: h,
    targetControlX: p,
    targetControlY: m
  });
  return [
    `M${t},${r} C${f},${h} ${p},${m} ${l},${u}`,
    v,
    g,
    y,
    x
  ];
}
function _g(t) {
  return q.memo(({ id: r, sourceX: i, sourceY: l, targetX: u, targetY: c, sourcePosition: f, targetPosition: h, label: p, labelStyle: m, labelShowBg: v, labelBgStyle: g, labelBgPadding: y, labelBgBorderRadius: x, style: S, markerEnd: M, markerStart: N, interactionWidth: k }) => {
    const [$, _, E] = Sg({
      sourceX: i,
      sourceY: l,
      sourcePosition: f,
      targetX: u,
      targetY: c,
      targetPosition: h
    }), D = t.isInternal ? void 0 : r;
    return I.jsx(vi, { id: D, path: $, labelX: _, labelY: E, label: p, labelStyle: m, labelShowBg: v, labelBgStyle: g, labelBgPadding: y, labelBgBorderRadius: x, style: S, markerEnd: M, markerStart: N, interactionWidth: k });
  });
}
const TS = _g({ isInternal: !1 }), Eg = _g({ isInternal: !0 });
TS.displayName = "SimpleBezierEdge";
Eg.displayName = "SimpleBezierEdgeInternal";
function kg(t) {
  return q.memo(({ id: r, sourceX: i, sourceY: l, targetX: u, targetY: c, label: f, labelStyle: h, labelShowBg: p, labelBgStyle: m, labelBgPadding: v, labelBgBorderRadius: g, style: y, sourcePosition: x = me.Bottom, targetPosition: S = me.Top, markerEnd: M, markerStart: N, pathOptions: k, interactionWidth: $ }) => {
    const [_, E, D] = ul({
      sourceX: i,
      sourceY: l,
      sourcePosition: x,
      targetX: u,
      targetY: c,
      targetPosition: S,
      borderRadius: k == null ? void 0 : k.borderRadius,
      offset: k == null ? void 0 : k.offset,
      stepPosition: k == null ? void 0 : k.stepPosition
    }), j = t.isInternal ? void 0 : r;
    return I.jsx(vi, { id: j, path: _, labelX: E, labelY: D, label: f, labelStyle: h, labelShowBg: p, labelBgStyle: m, labelBgPadding: v, labelBgBorderRadius: g, style: y, markerEnd: M, markerStart: N, interactionWidth: $ });
  });
}
const Ng = kg({ isInternal: !1 }), Cg = kg({ isInternal: !0 });
Ng.displayName = "SmoothStepEdge";
Cg.displayName = "SmoothStepEdgeInternal";
function Mg(t) {
  return q.memo(({ id: r, ...i }) => {
    var u;
    const l = t.isInternal ? void 0 : r;
    return I.jsx(Ng, { ...i, id: l, pathOptions: q.useMemo(() => {
      var c;
      return { borderRadius: 0, offset: (c = i.pathOptions) == null ? void 0 : c.offset };
    }, [(u = i.pathOptions) == null ? void 0 : u.offset]) });
  });
}
const LS = Mg({ isInternal: !1 }), Pg = Mg({ isInternal: !0 });
LS.displayName = "StepEdge";
Pg.displayName = "StepEdgeInternal";
function Ig(t) {
  return q.memo(({ id: r, sourceX: i, sourceY: l, targetX: u, targetY: c, label: f, labelStyle: h, labelShowBg: p, labelBgStyle: m, labelBgPadding: v, labelBgBorderRadius: g, style: y, markerEnd: x, markerStart: S, interactionWidth: M }) => {
    const [N, k, $] = Qp({ sourceX: i, sourceY: l, targetX: u, targetY: c }), _ = t.isInternal ? void 0 : r;
    return I.jsx(vi, { id: _, path: N, labelX: k, labelY: $, label: f, labelStyle: h, labelShowBg: p, labelBgStyle: m, labelBgPadding: v, labelBgBorderRadius: g, style: y, markerEnd: x, markerStart: S, interactionWidth: M });
  });
}
const AS = Ig({ isInternal: !1 }), zg = Ig({ isInternal: !0 });
AS.displayName = "StraightEdge";
zg.displayName = "StraightEdgeInternal";
function Tg(t) {
  return q.memo(({ id: r, sourceX: i, sourceY: l, targetX: u, targetY: c, sourcePosition: f = me.Bottom, targetPosition: h = me.Top, label: p, labelStyle: m, labelShowBg: v, labelBgStyle: g, labelBgPadding: y, labelBgBorderRadius: x, style: S, markerEnd: M, markerStart: N, pathOptions: k, interactionWidth: $ }) => {
    const [_, E, D] = bp({
      sourceX: i,
      sourceY: l,
      sourcePosition: f,
      targetX: u,
      targetY: c,
      targetPosition: h,
      curvature: k == null ? void 0 : k.curvature
    }), j = t.isInternal ? void 0 : r;
    return I.jsx(vi, { id: j, path: _, labelX: E, labelY: D, label: p, labelStyle: m, labelShowBg: v, labelBgStyle: g, labelBgPadding: y, labelBgBorderRadius: x, style: S, markerEnd: M, markerStart: N, interactionWidth: $ });
  });
}
const RS = Tg({ isInternal: !1 }), Lg = Tg({ isInternal: !0 });
RS.displayName = "BezierEdge";
Lg.displayName = "BezierEdgeInternal";
const Yh = {
  default: Lg,
  straight: zg,
  step: Pg,
  smoothstep: Cg,
  simplebezier: Eg
}, bh = {
  sourceX: null,
  sourceY: null,
  targetX: null,
  targetY: null,
  sourcePosition: null,
  targetPosition: null
}, $S = (t, r, i) => i === me.Left ? t - r : i === me.Right ? t + r : t, DS = (t, r, i) => i === me.Top ? t - r : i === me.Bottom ? t + r : t, Xh = "react-flow__edgeupdater";
function Qh({ position: t, centerX: r, centerY: i, radius: l = 10, onMouseDown: u, onMouseEnter: c, onMouseOut: f, type: h }) {
  return I.jsx("circle", { onMouseDown: u, onMouseEnter: c, onMouseOut: f, className: Ye([Xh, `${Xh}-${h}`]), cx: $S(r, l, t), cy: DS(i, l, t), r: l, stroke: "transparent", fill: "transparent" });
}
function jS({ isReconnectable: t, reconnectRadius: r, edge: i, sourceX: l, sourceY: u, targetX: c, targetY: f, sourcePosition: h, targetPosition: p, onReconnect: m, onReconnectStart: v, onReconnectEnd: g, setReconnecting: y, setUpdateHover: x }) {
  const S = je(), M = (E, D) => {
    if (E.button !== 0)
      return;
    const { autoPanOnConnect: j, domNode: H, connectionMode: b, connectionRadius: K, lib: ee, onConnectStart: U, cancelConnection: G, nodeLookup: Q, rfId: z, panBy: B, updateConnection: F } = S.getState(), W = D.type === "target", T = (C, A) => {
      y(!1), g == null || g(C, i, D.type, A);
    }, L = (C) => m == null ? void 0 : m(i, C), V = (C, A) => {
      y(!0), v == null || v(E, i, D.type), U == null || U(C, A);
    };
    Ya.onPointerDown(E.nativeEvent, {
      autoPanOnConnect: j,
      connectionMode: b,
      connectionRadius: K,
      domNode: H,
      handleId: D.id,
      nodeId: D.nodeId,
      nodeLookup: Q,
      isTarget: W,
      edgeUpdaterType: D.type,
      lib: ee,
      flowId: z,
      cancelConnection: G,
      panBy: B,
      isValidConnection: (...C) => {
        var A, te;
        return ((te = (A = S.getState()).isValidConnection) == null ? void 0 : te.call(A, ...C)) ?? !0;
      },
      onConnect: L,
      onConnectStart: V,
      onConnectEnd: (...C) => {
        var A, te;
        return (te = (A = S.getState()).onConnectEnd) == null ? void 0 : te.call(A, ...C);
      },
      onReconnectEnd: T,
      updateConnection: F,
      getTransform: () => S.getState().transform,
      getFromHandle: () => S.getState().connection.fromHandle,
      dragThreshold: S.getState().connectionDragThreshold,
      handleDomNode: E.currentTarget
    });
  }, N = (E) => M(E, { nodeId: i.target, id: i.targetHandle ?? null, type: "target" }), k = (E) => M(E, { nodeId: i.source, id: i.sourceHandle ?? null, type: "source" }), $ = () => x(!0), _ = () => x(!1);
  return I.jsxs(I.Fragment, { children: [(t === !0 || t === "source") && I.jsx(Qh, { position: h, centerX: l, centerY: u, radius: r, onMouseDown: N, onMouseEnter: $, onMouseOut: _, type: "source" }), (t === !0 || t === "target") && I.jsx(Qh, { position: p, centerX: c, centerY: f, radius: r, onMouseDown: k, onMouseEnter: $, onMouseOut: _, type: "target" })] });
}
function OS({ id: t, edgesFocusable: r, edgesReconnectable: i, elementsSelectable: l, onClick: u, onDoubleClick: c, onContextMenu: f, onMouseEnter: h, onMouseMove: p, onMouseLeave: m, reconnectRadius: v, onReconnect: g, onReconnectStart: y, onReconnectEnd: x, rfId: S, edgeTypes: M, noPanClassName: N, onError: k, disableKeyboardA11y: $ }) {
  let _ = Me((Se) => Se.edgeLookup.get(t));
  const E = Me((Se) => Se.defaultEdgeOptions);
  _ = E ? { ...E, ..._ } : _;
  let D = _.type || "default", j = (M == null ? void 0 : M[D]) || Yh[D];
  j === void 0 && (k == null || k("011", Kt.error011(D)), D = "default", j = (M == null ? void 0 : M.default) || Yh.default);
  const H = !!(_.focusable || r && typeof _.focusable > "u"), b = typeof g < "u" && (_.reconnectable || i && typeof _.reconnectable > "u"), K = !!(_.selectable || l && typeof _.selectable > "u"), ee = q.useRef(null), [U, G] = q.useState(!1), [Q, z] = q.useState(!1), B = je(), { zIndex: F, sourceX: W, sourceY: T, targetX: L, targetY: V, sourcePosition: C, targetPosition: A } = Me(q.useCallback((Se) => {
    const ye = Se.nodeLookup.get(_.source), Ne = Se.nodeLookup.get(_.target);
    if (!ye || !Ne)
      return {
        zIndex: _.zIndex,
        ...bh
      };
    const Te = Px({
      id: t,
      sourceNode: ye,
      targetNode: Ne,
      sourceHandle: _.sourceHandle || null,
      targetHandle: _.targetHandle || null,
      connectionMode: Se.connectionMode,
      onError: k
    });
    return {
      zIndex: xx({
        selected: _.selected,
        zIndex: _.zIndex,
        sourceNode: ye,
        targetNode: Ne,
        elevateOnSelect: Se.elevateEdgesOnSelect,
        zIndexMode: Se.zIndexMode
      }),
      ...Te || bh
    };
  }, [_.source, _.target, _.sourceHandle, _.targetHandle, _.selected, _.zIndex]), $e), te = q.useMemo(() => _.markerStart ? `url('#${Wa(_.markerStart, S)}')` : void 0, [_.markerStart, S]), re = q.useMemo(() => _.markerEnd ? `url('#${Wa(_.markerEnd, S)}')` : void 0, [_.markerEnd, S]);
  if (_.hidden || W === null || T === null || L === null || V === null)
    return null;
  const le = (Se) => {
    var Ce;
    const { addSelectedEdges: ye, unselectNodesAndEdges: Ne, multiSelectionActive: Te } = B.getState();
    K && (B.setState({ nodesSelectionActive: !1 }), _.selected && Te ? (Ne({ nodes: [], edges: [_] }), (Ce = ee.current) == null || Ce.blur()) : ye([t])), u && u(Se, _);
  }, ue = c ? (Se) => {
    c(Se, { ..._ });
  } : void 0, ae = f ? (Se) => {
    f(Se, { ..._ });
  } : void 0, J = h ? (Se) => {
    h(Se, { ..._ });
  } : void 0, ce = p ? (Se) => {
    p(Se, { ..._ });
  } : void 0, xe = m ? (Se) => {
    m(Se, { ..._ });
  } : void 0, _e = (Se) => {
    var ye;
    if (!$ && Tp.includes(Se.key) && K) {
      const { unselectNodesAndEdges: Ne, addSelectedEdges: Te } = B.getState();
      Se.key === "Escape" ? ((ye = ee.current) == null || ye.blur(), Ne({ edges: [_] })) : Te([t]);
    }
  };
  return I.jsx("svg", { style: { zIndex: F }, children: I.jsxs("g", { className: Ye([
    "react-flow__edge",
    `react-flow__edge-${D}`,
    _.className,
    N,
    {
      selected: _.selected,
      animated: _.animated,
      inactive: !K && !u,
      updating: U,
      selectable: K
    }
  ]), onClick: le, onDoubleClick: ue, onContextMenu: ae, onMouseEnter: J, onMouseMove: ce, onMouseLeave: xe, onKeyDown: H ? _e : void 0, tabIndex: H ? 0 : void 0, role: _.ariaRole ?? (H ? "group" : "img"), "aria-roledescription": "edge", "data-id": t, "data-testid": `rf__edge-${t}`, "aria-label": _.ariaLabel === null ? void 0 : _.ariaLabel || `Edge from ${_.source} to ${_.target}`, "aria-describedby": H ? `${ag}-${S}` : void 0, ref: ee, ..._.domAttributes, children: [!Q && I.jsx(j, { id: t, source: _.source, target: _.target, type: _.type, selected: _.selected, animated: _.animated, selectable: K, deletable: _.deletable ?? !0, label: _.label, labelStyle: _.labelStyle, labelShowBg: _.labelShowBg, labelBgStyle: _.labelBgStyle, labelBgPadding: _.labelBgPadding, labelBgBorderRadius: _.labelBgBorderRadius, sourceX: W, sourceY: T, targetX: L, targetY: V, sourcePosition: C, targetPosition: A, data: _.data, style: _.style, sourceHandleId: _.sourceHandle, targetHandleId: _.targetHandle, markerStart: te, markerEnd: re, pathOptions: "pathOptions" in _ ? _.pathOptions : void 0, interactionWidth: _.interactionWidth }), b && I.jsx(jS, { edge: _, isReconnectable: b, reconnectRadius: v, onReconnect: g, onReconnectStart: y, onReconnectEnd: x, sourceX: W, sourceY: T, targetX: L, targetY: V, sourcePosition: C, targetPosition: A, setUpdateHover: G, setReconnecting: z })] }) });
}
var FS = q.memo(OS);
const HS = (t) => ({
  edgesFocusable: t.edgesFocusable,
  edgesReconnectable: t.edgesReconnectable,
  elementsSelectable: t.elementsSelectable,
  connectionMode: t.connectionMode,
  onError: t.onError
});
function Ag({ defaultMarkerColor: t, onlyRenderVisibleElements: r, rfId: i, edgeTypes: l, noPanClassName: u, onReconnect: c, onEdgeContextMenu: f, onEdgeMouseEnter: h, onEdgeMouseMove: p, onEdgeMouseLeave: m, onEdgeClick: v, reconnectRadius: g, onEdgeDoubleClick: y, onReconnectStart: x, onReconnectEnd: S, disableKeyboardA11y: M }) {
  const { edgesFocusable: N, edgesReconnectable: k, elementsSelectable: $, onError: _ } = Me(HS, $e), E = kS(r);
  return I.jsxs("div", { className: "react-flow__edges", children: [I.jsx(IS, { defaultColor: t, rfId: i }), E.map((D) => I.jsx(FS, { id: D, edgesFocusable: N, edgesReconnectable: k, elementsSelectable: $, noPanClassName: u, onReconnect: c, onContextMenu: f, onMouseEnter: h, onMouseMove: p, onMouseLeave: m, onClick: v, reconnectRadius: g, onDoubleClick: y, onReconnectStart: x, onReconnectEnd: S, rfId: i, onError: _, edgeTypes: l, disableKeyboardA11y: M }, D))] });
}
Ag.displayName = "EdgeRenderer";
const VS = q.memo(Ag), BS = (t) => `translate(${t.transform[0]}px,${t.transform[1]}px) scale(${t.transform[2]})`;
function WS({ children: t }) {
  const r = Me(BS);
  return I.jsx("div", { className: "react-flow__viewport xyflow__viewport react-flow__container", style: { transform: r }, children: t });
}
function US(t) {
  const r = _l(), i = q.useRef(!1);
  q.useEffect(() => {
    !i.current && r.viewportInitialized && t && (setTimeout(() => t(r), 1), i.current = !0);
  }, [t, r.viewportInitialized]);
}
const YS = (t) => {
  var r;
  return (r = t.panZoom) == null ? void 0 : r.syncViewport;
};
function bS(t) {
  const r = Me(YS), i = je();
  return q.useEffect(() => {
    t && (r == null || r(t), i.setState({ transform: [t.x, t.y, t.zoom] }));
  }, [t, r]), null;
}
function XS(t) {
  return t.connection.inProgress ? { ...t.connection, to: yi(t.connection.to, t.transform) } : { ...t.connection };
}
function QS(t) {
  return XS;
}
function KS(t) {
  const r = QS();
  return Me(r, $e);
}
const GS = (t) => ({
  nodesConnectable: t.nodesConnectable,
  isValid: t.connection.isValid,
  inProgress: t.connection.inProgress,
  width: t.width,
  height: t.height
});
function ZS({ containerStyle: t, style: r, type: i, component: l }) {
  const { nodesConnectable: u, width: c, height: f, isValid: h, inProgress: p } = Me(GS, $e);
  return !(c && u && p) ? null : I.jsx("svg", { style: t, width: c, height: f, className: "react-flow__connectionline react-flow__container", children: I.jsx("g", { className: Ye(["react-flow__connection", Rp(h)]), children: I.jsx(Rg, { style: r, type: i, CustomComponent: l, isValid: h }) }) });
}
const Rg = ({ style: t, type: r = On.Bezier, CustomComponent: i, isValid: l }) => {
  const { inProgress: u, from: c, fromNode: f, fromHandle: h, fromPosition: p, to: m, toNode: v, toHandle: g, toPosition: y, pointer: x } = KS();
  if (!u)
    return;
  if (i)
    return I.jsx(i, { connectionLineType: r, connectionLineStyle: t, fromNode: f, fromHandle: h, fromX: c.x, fromY: c.y, toX: m.x, toY: m.y, fromPosition: p, toPosition: y, connectionStatus: Rp(l), toNode: v, toHandle: g, pointer: x });
  let S = "";
  const M = {
    sourceX: c.x,
    sourceY: c.y,
    sourcePosition: p,
    targetX: m.x,
    targetY: m.y,
    targetPosition: y
  };
  switch (r) {
    case On.Bezier:
      [S] = bp(M);
      break;
    case On.SimpleBezier:
      [S] = Sg(M);
      break;
    case On.Step:
      [S] = ul({
        ...M,
        borderRadius: 0
      });
      break;
    case On.SmoothStep:
      [S] = ul(M);
      break;
    default:
      [S] = Qp(M);
  }
  return I.jsx("path", { d: S, fill: "none", className: "react-flow__connection-path", style: t });
};
Rg.displayName = "ConnectionLine";
const qS = {};
function Kh(t = qS) {
  q.useRef(t), je(), q.useEffect(() => {
  }, [t]);
}
function JS() {
  je(), q.useRef(!1), q.useEffect(() => {
  }, []);
}
function $g({ nodeTypes: t, edgeTypes: r, onInit: i, onNodeClick: l, onEdgeClick: u, onNodeDoubleClick: c, onEdgeDoubleClick: f, onNodeMouseEnter: h, onNodeMouseMove: p, onNodeMouseLeave: m, onNodeContextMenu: v, onSelectionContextMenu: g, onSelectionStart: y, onSelectionEnd: x, connectionLineType: S, connectionLineStyle: M, connectionLineComponent: N, connectionLineContainerStyle: k, selectionKeyCode: $, selectionOnDrag: _, selectionMode: E, multiSelectionKeyCode: D, panActivationKeyCode: j, zoomActivationKeyCode: H, deleteKeyCode: b, onlyRenderVisibleElements: K, elementsSelectable: ee, defaultViewport: U, translateExtent: G, minZoom: Q, maxZoom: z, preventScrolling: B, defaultMarkerColor: F, zoomOnScroll: W, zoomOnPinch: T, panOnScroll: L, panOnScrollSpeed: V, panOnScrollMode: C, zoomOnDoubleClick: A, panOnDrag: te, onPaneClick: re, onPaneMouseEnter: le, onPaneMouseMove: ue, onPaneMouseLeave: ae, onPaneScroll: J, onPaneContextMenu: ce, paneClickDistance: xe, nodeClickDistance: _e, onEdgeContextMenu: Se, onEdgeMouseEnter: ye, onEdgeMouseMove: Ne, onEdgeMouseLeave: Te, reconnectRadius: Ce, onReconnect: We, onReconnectStart: It, onReconnectEnd: dt, noDragClassName: ht, noWheelClassName: St, noPanClassName: Zt, disableKeyboardA11y: pn, nodeExtent: pr, rfId: Hn, viewport: qt, onViewportChange: Jt }) {
  return Kh(t), Kh(r), JS(), US(i), bS(qt), I.jsx(pS, { onPaneClick: re, onPaneMouseEnter: le, onPaneMouseMove: ue, onPaneMouseLeave: ae, onPaneContextMenu: ce, onPaneScroll: J, paneClickDistance: xe, deleteKeyCode: b, selectionKeyCode: $, selectionOnDrag: _, selectionMode: E, onSelectionStart: y, onSelectionEnd: x, multiSelectionKeyCode: D, panActivationKeyCode: j, zoomActivationKeyCode: H, elementsSelectable: ee, zoomOnScroll: W, zoomOnPinch: T, zoomOnDoubleClick: A, panOnScroll: L, panOnScrollSpeed: V, panOnScrollMode: C, panOnDrag: te, defaultViewport: U, translateExtent: G, minZoom: Q, maxZoom: z, onSelectionContextMenu: g, preventScrolling: B, noDragClassName: ht, noWheelClassName: St, noPanClassName: Zt, disableKeyboardA11y: pn, onViewportChange: Jt, isControlledViewport: !!qt, children: I.jsxs(WS, { children: [I.jsx(VS, { edgeTypes: r, onEdgeClick: u, onEdgeDoubleClick: f, onReconnect: We, onReconnectStart: It, onReconnectEnd: dt, onlyRenderVisibleElements: K, onEdgeContextMenu: Se, onEdgeMouseEnter: ye, onEdgeMouseMove: Ne, onEdgeMouseLeave: Te, reconnectRadius: Ce, defaultMarkerColor: F, noPanClassName: Zt, disableKeyboardA11y: pn, rfId: Hn }), I.jsx(ZS, { style: M, type: S, component: N, containerStyle: k }), I.jsx("div", { className: "react-flow__edgelabel-renderer" }), I.jsx(ES, { nodeTypes: t, onNodeClick: l, onNodeDoubleClick: c, onNodeMouseEnter: h, onNodeMouseMove: p, onNodeMouseLeave: m, onNodeContextMenu: v, nodeClickDistance: _e, onlyRenderVisibleElements: K, noPanClassName: Zt, noDragClassName: ht, disableKeyboardA11y: pn, nodeExtent: pr, rfId: Hn }), I.jsx("div", { className: "react-flow__viewport-portal" })] }) });
}
$g.displayName = "GraphView";
const e_ = q.memo($g), Gh = ({ nodes: t, edges: r, defaultNodes: i, defaultEdges: l, width: u, height: c, fitView: f, fitViewOptions: h, minZoom: p = 0.5, maxZoom: m = 2, nodeOrigin: v, nodeExtent: g, zIndexMode: y = "basic" } = {}) => {
  const x = /* @__PURE__ */ new Map(), S = /* @__PURE__ */ new Map(), M = /* @__PURE__ */ new Map(), N = /* @__PURE__ */ new Map(), k = l ?? r ?? [], $ = i ?? t ?? [], _ = v ?? [0, 0], E = g ?? ii;
  Zp(M, N, k);
  const { nodesInitialized: D } = Ua($, x, S, {
    nodeOrigin: _,
    nodeExtent: E,
    zIndexMode: y
  });
  let j = [0, 0, 1];
  if (f && u && c) {
    const H = gi(x, {
      filter: (U) => !!((U.width || U.initialWidth) && (U.height || U.initialHeight))
    }), { x: b, y: K, zoom: ee } = oc(H, u, c, p, m, (h == null ? void 0 : h.padding) ?? 0.1);
    j = [b, K, ee];
  }
  return {
    rfId: "1",
    width: u ?? 0,
    height: c ?? 0,
    transform: j,
    nodes: $,
    nodesInitialized: D,
    nodeLookup: x,
    parentLookup: S,
    edges: k,
    edgeLookup: N,
    connectionLookup: M,
    onNodesChange: null,
    onEdgesChange: null,
    hasDefaultNodes: i !== void 0,
    hasDefaultEdges: l !== void 0,
    panZoom: null,
    minZoom: p,
    maxZoom: m,
    translateExtent: ii,
    nodeExtent: E,
    nodesSelectionActive: !1,
    userSelectionActive: !1,
    userSelectionRect: null,
    connectionMode: Zr.Strict,
    domNode: null,
    paneDragging: !1,
    noPanClassName: "nopan",
    nodeOrigin: _,
    nodeDragThreshold: 1,
    connectionDragThreshold: 1,
    snapGrid: [15, 15],
    snapToGrid: !1,
    nodesDraggable: !0,
    nodesConnectable: !0,
    nodesFocusable: !0,
    edgesFocusable: !0,
    edgesReconnectable: !0,
    elementsSelectable: !0,
    elevateNodesOnSelect: !0,
    elevateEdgesOnSelect: !0,
    selectNodesOnDrag: !0,
    multiSelectionActive: !1,
    fitViewQueued: f ?? !1,
    fitViewOptions: h,
    fitViewResolver: null,
    connection: { ...Ap },
    connectionClickStartHandle: null,
    connectOnClick: !0,
    ariaLiveMessage: "",
    autoPanOnConnect: !0,
    autoPanOnNodeDrag: !0,
    autoPanOnNodeFocus: !0,
    autoPanSpeed: 15,
    connectionRadius: 20,
    onError: px,
    isValidConnection: void 0,
    onSelectionChangeHandlers: [],
    lib: "react",
    debug: !1,
    ariaLabelConfig: Lp,
    zIndexMode: y,
    onNodesChangeMiddlewareMap: /* @__PURE__ */ new Map(),
    onEdgesChangeMiddlewareMap: /* @__PURE__ */ new Map()
  };
}, t_ = ({ nodes: t, edges: r, defaultNodes: i, defaultEdges: l, width: u, height: c, fitView: f, fitViewOptions: h, minZoom: p, maxZoom: m, nodeOrigin: v, nodeExtent: g, zIndexMode: y }) => v1((x, S) => {
  async function M() {
    const { nodeLookup: N, panZoom: k, fitViewOptions: $, fitViewResolver: _, width: E, height: D, minZoom: j, maxZoom: H } = S();
    k && (await dx({
      nodes: N,
      width: E,
      height: D,
      panZoom: k,
      minZoom: j,
      maxZoom: H
    }, $), _ == null || _.resolve(!0), x({ fitViewResolver: null }));
  }
  return {
    ...Gh({
      nodes: t,
      edges: r,
      width: u,
      height: c,
      fitView: f,
      fitViewOptions: h,
      minZoom: p,
      maxZoom: m,
      nodeOrigin: v,
      nodeExtent: g,
      defaultNodes: i,
      defaultEdges: l,
      zIndexMode: y
    }),
    setNodes: (N) => {
      const { nodeLookup: k, parentLookup: $, nodeOrigin: _, elevateNodesOnSelect: E, fitViewQueued: D, zIndexMode: j, nodesSelectionActive: H } = S(), { nodesInitialized: b, hasSelectedNodes: K } = Ua(N, k, $, {
        nodeOrigin: _,
        nodeExtent: g,
        elevateNodesOnSelect: E,
        checkEquality: !0,
        zIndexMode: j
      }), ee = H && K;
      D && b ? (M(), x({
        nodes: N,
        nodesInitialized: b,
        fitViewQueued: !1,
        fitViewOptions: void 0,
        nodesSelectionActive: ee
      })) : x({ nodes: N, nodesInitialized: b, nodesSelectionActive: ee });
    },
    setEdges: (N) => {
      const { connectionLookup: k, edgeLookup: $ } = S();
      Zp(k, $, N), x({ edges: N });
    },
    setDefaultNodesAndEdges: (N, k) => {
      if (N) {
        const { setNodes: $ } = S();
        $(N), x({ hasDefaultNodes: !0 });
      }
      if (k) {
        const { setEdges: $ } = S();
        $(k), x({ hasDefaultEdges: !0 });
      }
    },
    /*
     * Every node gets registered at a ResizeObserver. Whenever a node
     * changes its dimensions, this function is called to measure the
     * new dimensions and update the nodes.
     */
    updateNodeInternals: (N) => {
      const { triggerNodeChanges: k, nodeLookup: $, parentLookup: _, domNode: E, nodeOrigin: D, nodeExtent: j, debug: H, fitViewQueued: b, zIndexMode: K } = S(), { changes: ee, updatedInternals: U } = Dx(N, $, _, E, D, j, K);
      U && (Lx($, _, { nodeOrigin: D, nodeExtent: j, zIndexMode: K }), b ? (M(), x({ fitViewQueued: !1, fitViewOptions: void 0 })) : x({}), (ee == null ? void 0 : ee.length) > 0 && (H && console.log("React Flow: trigger node changes", ee), k == null || k(ee)));
    },
    updateNodePositions: (N, k = !1) => {
      const $ = [];
      let _ = [];
      const { nodeLookup: E, triggerNodeChanges: D, connection: j, updateConnection: H, onNodesChangeMiddlewareMap: b } = S();
      for (const [K, ee] of N) {
        const U = E.get(K), G = !!(U != null && U.expandParent && (U != null && U.parentId) && (ee != null && ee.position)), Q = {
          id: K,
          type: "position",
          position: G ? {
            x: Math.max(0, ee.position.x),
            y: Math.max(0, ee.position.y)
          } : ee.position,
          dragging: k
        };
        if (U && j.inProgress && j.fromNode.id === U.id) {
          const z = hr(U, j.fromHandle, me.Left, !0);
          H({ ...j, from: z });
        }
        G && U.parentId && $.push({
          id: K,
          parentId: U.parentId,
          rect: {
            ...ee.internals.positionAbsolute,
            width: ee.measured.width ?? 0,
            height: ee.measured.height ?? 0
          }
        }), _.push(Q);
      }
      if ($.length > 0) {
        const { parentLookup: K, nodeOrigin: ee } = S(), U = cc($, E, K, ee);
        _.push(...U);
      }
      for (const K of b.values())
        _ = K(_);
      D(_);
    },
    triggerNodeChanges: (N) => {
      const { onNodesChange: k, setNodes: $, nodes: _, hasDefaultNodes: E, debug: D } = S();
      if (N != null && N.length) {
        if (E) {
          const j = F1(N, _);
          $(j);
        }
        D && console.log("React Flow: trigger node changes", N), k == null || k(N);
      }
    },
    triggerEdgeChanges: (N) => {
      const { onEdgesChange: k, setEdges: $, edges: _, hasDefaultEdges: E, debug: D } = S();
      if (N != null && N.length) {
        if (E) {
          const j = H1(N, _);
          $(j);
        }
        D && console.log("React Flow: trigger edge changes", N), k == null || k(N);
      }
    },
    addSelectedNodes: (N) => {
      const { multiSelectionActive: k, edgeLookup: $, nodeLookup: _, triggerNodeChanges: E, triggerEdgeChanges: D } = S();
      if (k) {
        const j = N.map((H) => sr(H, !0));
        E(j);
        return;
      }
      E(br(_, /* @__PURE__ */ new Set([...N]), !0)), D(br($));
    },
    addSelectedEdges: (N) => {
      const { multiSelectionActive: k, edgeLookup: $, nodeLookup: _, triggerNodeChanges: E, triggerEdgeChanges: D } = S();
      if (k) {
        const j = N.map((H) => sr(H, !0));
        D(j);
        return;
      }
      D(br($, /* @__PURE__ */ new Set([...N]))), E(br(_, /* @__PURE__ */ new Set(), !0));
    },
    unselectNodesAndEdges: ({ nodes: N, edges: k } = {}) => {
      const { edges: $, nodes: _, nodeLookup: E, triggerNodeChanges: D, triggerEdgeChanges: j } = S(), H = N || _, b = k || $, K = [];
      for (const U of H) {
        if (!U.selected)
          continue;
        const G = E.get(U.id);
        G && (G.selected = !1), K.push(sr(U.id, !1));
      }
      const ee = [];
      for (const U of b)
        U.selected && ee.push(sr(U.id, !1));
      D(K), j(ee);
    },
    setMinZoom: (N) => {
      const { panZoom: k, maxZoom: $ } = S();
      k == null || k.setScaleExtent([N, $]), x({ minZoom: N });
    },
    setMaxZoom: (N) => {
      const { panZoom: k, minZoom: $ } = S();
      k == null || k.setScaleExtent([$, N]), x({ maxZoom: N });
    },
    setTranslateExtent: (N) => {
      var k;
      (k = S().panZoom) == null || k.setTranslateExtent(N), x({ translateExtent: N });
    },
    resetSelectedElements: () => {
      const { edges: N, nodes: k, triggerNodeChanges: $, triggerEdgeChanges: _, elementsSelectable: E } = S();
      if (!E)
        return;
      const D = k.reduce((H, b) => b.selected ? [...H, sr(b.id, !1)] : H, []), j = N.reduce((H, b) => b.selected ? [...H, sr(b.id, !1)] : H, []);
      $(D), _(j);
    },
    setNodeExtent: (N) => {
      const { nodes: k, nodeLookup: $, parentLookup: _, nodeOrigin: E, elevateNodesOnSelect: D, nodeExtent: j, zIndexMode: H } = S();
      N[0][0] === j[0][0] && N[0][1] === j[0][1] && N[1][0] === j[1][0] && N[1][1] === j[1][1] || (Ua(k, $, _, {
        nodeOrigin: E,
        nodeExtent: N,
        elevateNodesOnSelect: D,
        checkEquality: !1,
        zIndexMode: H
      }), x({ nodeExtent: N }));
    },
    panBy: (N) => {
      const { transform: k, width: $, height: _, panZoom: E, translateExtent: D } = S();
      return jx({ delta: N, panZoom: E, transform: k, translateExtent: D, width: $, height: _ });
    },
    setCenter: async (N, k, $) => {
      const { width: _, height: E, maxZoom: D, panZoom: j } = S();
      if (!j)
        return Promise.resolve(!1);
      const H = typeof ($ == null ? void 0 : $.zoom) < "u" ? $.zoom : D;
      return await j.setViewport({
        x: _ / 2 - N * H,
        y: E / 2 - k * H,
        zoom: H
      }, { duration: $ == null ? void 0 : $.duration, ease: $ == null ? void 0 : $.ease, interpolate: $ == null ? void 0 : $.interpolate }), Promise.resolve(!0);
    },
    cancelConnection: () => {
      x({
        connection: { ...Ap }
      });
    },
    updateConnection: (N) => {
      x({ connection: N });
    },
    reset: () => x({ ...Gh() })
  };
}, Object.is);
function Dg({ initialNodes: t, initialEdges: r, defaultNodes: i, defaultEdges: l, initialWidth: u, initialHeight: c, initialMinZoom: f, initialMaxZoom: h, initialFitViewOptions: p, fitView: m, nodeOrigin: v, nodeExtent: g, zIndexMode: y, children: x }) {
  const [S] = q.useState(() => t_({
    nodes: t,
    edges: r,
    defaultNodes: i,
    defaultEdges: l,
    width: u,
    height: c,
    fitView: m,
    minZoom: f,
    maxZoom: h,
    fitViewOptions: p,
    nodeOrigin: v,
    nodeExtent: g,
    zIndexMode: y
  }));
  return I.jsx(w1, { value: S, children: I.jsx(W1, { children: x }) });
}
function n_({ children: t, nodes: r, edges: i, defaultNodes: l, defaultEdges: u, width: c, height: f, fitView: h, fitViewOptions: p, minZoom: m, maxZoom: v, nodeOrigin: g, nodeExtent: y, zIndexMode: x }) {
  return q.useContext(xl) ? I.jsx(I.Fragment, { children: t }) : I.jsx(Dg, { initialNodes: r, initialEdges: i, defaultNodes: l, defaultEdges: u, initialWidth: c, initialHeight: f, fitView: h, initialFitViewOptions: p, initialMinZoom: m, initialMaxZoom: v, nodeOrigin: g, nodeExtent: y, zIndexMode: x, children: t });
}
const r_ = {
  width: "100%",
  height: "100%",
  overflow: "hidden",
  position: "relative",
  zIndex: 0
};
function o_({ nodes: t, edges: r, defaultNodes: i, defaultEdges: l, className: u, nodeTypes: c, edgeTypes: f, onNodeClick: h, onEdgeClick: p, onInit: m, onMove: v, onMoveStart: g, onMoveEnd: y, onConnect: x, onConnectStart: S, onConnectEnd: M, onClickConnectStart: N, onClickConnectEnd: k, onNodeMouseEnter: $, onNodeMouseMove: _, onNodeMouseLeave: E, onNodeContextMenu: D, onNodeDoubleClick: j, onNodeDragStart: H, onNodeDrag: b, onNodeDragStop: K, onNodesDelete: ee, onEdgesDelete: U, onDelete: G, onSelectionChange: Q, onSelectionDragStart: z, onSelectionDrag: B, onSelectionDragStop: F, onSelectionContextMenu: W, onSelectionStart: T, onSelectionEnd: L, onBeforeDelete: V, connectionMode: C, connectionLineType: A = On.Bezier, connectionLineStyle: te, connectionLineComponent: re, connectionLineContainerStyle: le, deleteKeyCode: ue = "Backspace", selectionKeyCode: ae = "Shift", selectionOnDrag: J = !1, selectionMode: ce = si.Full, panActivationKeyCode: xe = "Space", multiSelectionKeyCode: _e = ai() ? "Meta" : "Control", zoomActivationKeyCode: Se = ai() ? "Meta" : "Control", snapToGrid: ye, snapGrid: Ne, onlyRenderVisibleElements: Te = !1, selectNodesOnDrag: Ce, nodesDraggable: We, autoPanOnNodeFocus: It, nodesConnectable: dt, nodesFocusable: ht, nodeOrigin: St = cg, edgesFocusable: Zt, edgesReconnectable: pn, elementsSelectable: pr = !0, defaultViewport: Hn = L1, minZoom: qt = 0.5, maxZoom: Jt = 2, translateExtent: Vn = ii, preventScrolling: wi = !0, nodeExtent: en, defaultMarkerColor: Bn = "#b1b1b7", zoomOnScroll: kl = !0, zoomOnPinch: xi = !0, panOnScroll: Si = !1, panOnScrollSpeed: Nl = 0.5, panOnScrollMode: ro = ar.Free, zoomOnDoubleClick: oo = !0, panOnDrag: io = !0, onPaneClick: so, onPaneMouseEnter: lo, onPaneMouseMove: gn, onPaneMouseLeave: mn, onPaneScroll: _i, onPaneContextMenu: Ei, paneClickDistance: ki = 1, nodeClickDistance: Ni = 0, children: Ci, onReconnect: uo, onReconnectStart: Mi, onReconnectEnd: Wn, onEdgeContextMenu: ao, onEdgeDoubleClick: Un, onEdgeMouseEnter: Cl, onEdgeMouseMove: Yn, onEdgeMouseLeave: gr, reconnectRadius: mr = 10, onNodesChange: co, onEdgesChange: Ml, noDragClassName: Pl = "nodrag", noWheelClassName: Il = "nowheel", noPanClassName: Vt = "nopan", fitView: fo, fitViewOptions: ho, connectOnClick: zl, attributionPosition: Pi, proOptions: Ii, defaultEdgeOptions: zi, elevateNodesOnSelect: Ti = !0, elevateEdgesOnSelect: Tl = !1, disableKeyboardA11y: Li = !1, autoPanOnConnect: Oe, autoPanOnNodeDrag: Ll, autoPanSpeed: po, connectionRadius: Ai, isValidConnection: yr, onError: Al, style: Ri, id: bn, nodeDragThreshold: _t, connectionDragThreshold: Rl, viewport: pt, onViewportChange: $l, width: Dl, height: jl, colorMode: vr = "light", debug: wr, onScroll: Bt, ariaLabelConfig: xr, zIndexMode: $i = "basic", ...Ol }, go) {
  const Sr = bn || "1", mo = D1(vr), Xn = q.useCallback((Di) => {
    Di.currentTarget.scrollTo({ top: 0, left: 0, behavior: "instant" }), Bt == null || Bt(Di);
  }, [Bt]);
  return I.jsx("div", { "data-testid": "rf__wrapper", ...Ol, onScroll: Xn, style: { ...Ri, ...r_ }, ref: go, className: Ye(["react-flow", u, mo]), id: bn, role: "application", children: I.jsxs(n_, { nodes: t, edges: r, width: Dl, height: jl, fitView: fo, fitViewOptions: ho, minZoom: qt, maxZoom: Jt, nodeOrigin: St, nodeExtent: en, zIndexMode: $i, children: [I.jsx($1, { nodes: t, edges: r, defaultNodes: i, defaultEdges: l, onConnect: x, onConnectStart: S, onConnectEnd: M, onClickConnectStart: N, onClickConnectEnd: k, nodesDraggable: We, autoPanOnNodeFocus: It, nodesConnectable: dt, nodesFocusable: ht, edgesFocusable: Zt, edgesReconnectable: pn, elementsSelectable: pr, elevateNodesOnSelect: Ti, elevateEdgesOnSelect: Tl, minZoom: qt, maxZoom: Jt, nodeExtent: en, onNodesChange: co, onEdgesChange: Ml, snapToGrid: ye, snapGrid: Ne, connectionMode: C, translateExtent: Vn, connectOnClick: zl, defaultEdgeOptions: zi, fitView: fo, fitViewOptions: ho, onNodesDelete: ee, onEdgesDelete: U, onDelete: G, onNodeDragStart: H, onNodeDrag: b, onNodeDragStop: K, onSelectionDrag: B, onSelectionDragStart: z, onSelectionDragStop: F, onMove: v, onMoveStart: g, onMoveEnd: y, noPanClassName: Vt, nodeOrigin: St, rfId: Sr, autoPanOnConnect: Oe, autoPanOnNodeDrag: Ll, autoPanSpeed: po, onError: Al, connectionRadius: Ai, isValidConnection: yr, selectNodesOnDrag: Ce, nodeDragThreshold: _t, connectionDragThreshold: Rl, onBeforeDelete: V, debug: wr, ariaLabelConfig: xr, zIndexMode: $i }), I.jsx(e_, { onInit: m, onNodeClick: h, onEdgeClick: p, onNodeMouseEnter: $, onNodeMouseMove: _, onNodeMouseLeave: E, onNodeContextMenu: D, onNodeDoubleClick: j, nodeTypes: c, edgeTypes: f, connectionLineType: A, connectionLineStyle: te, connectionLineComponent: re, connectionLineContainerStyle: le, selectionKeyCode: ae, selectionOnDrag: J, selectionMode: ce, deleteKeyCode: ue, multiSelectionKeyCode: _e, panActivationKeyCode: xe, zoomActivationKeyCode: Se, onlyRenderVisibleElements: Te, defaultViewport: Hn, translateExtent: Vn, minZoom: qt, maxZoom: Jt, preventScrolling: wi, zoomOnScroll: kl, zoomOnPinch: xi, zoomOnDoubleClick: oo, panOnScroll: Si, panOnScrollSpeed: Nl, panOnScrollMode: ro, panOnDrag: io, onPaneClick: so, onPaneMouseEnter: lo, onPaneMouseMove: gn, onPaneMouseLeave: mn, onPaneScroll: _i, onPaneContextMenu: Ei, paneClickDistance: ki, nodeClickDistance: Ni, onSelectionContextMenu: W, onSelectionStart: T, onSelectionEnd: L, onReconnect: uo, onReconnectStart: Mi, onReconnectEnd: Wn, onEdgeContextMenu: ao, onEdgeDoubleClick: Un, onEdgeMouseEnter: Cl, onEdgeMouseMove: Yn, onEdgeMouseLeave: gr, reconnectRadius: mr, defaultMarkerColor: Bn, noDragClassName: Pl, noWheelClassName: Il, noPanClassName: Vt, rfId: Sr, disableKeyboardA11y: Li, nodeExtent: en, viewport: pt, onViewportChange: $l }), I.jsx(T1, { onSelectionChange: Q }), Ci, I.jsx(C1, { proOptions: Ii, position: Pi }), I.jsx(N1, { rfId: Sr, disableKeyboardA11y: Li })] }) });
}
var i_ = dg(o_);
const s_ = (t) => ({
  x: t.transform[0],
  y: t.transform[1],
  zoom: t.transform[2]
});
function l_() {
  return Me(s_, $e);
}
function u_({ dimensions: t, lineWidth: r, variant: i, className: l }) {
  return I.jsx("path", { strokeWidth: r, d: `M${t[0] / 2} 0 V${t[1]} M0 ${t[1] / 2} H${t[0]}`, className: Ye(["react-flow__background-pattern", i, l]) });
}
function a_({ radius: t, className: r }) {
  return I.jsx("circle", { cx: t, cy: t, r: t, className: Ye(["react-flow__background-pattern", "dots", r]) });
}
var Fn;
(function(t) {
  t.Lines = "lines", t.Dots = "dots", t.Cross = "cross";
})(Fn || (Fn = {}));
const c_ = {
  [Fn.Dots]: 1,
  [Fn.Lines]: 1,
  [Fn.Cross]: 6
}, f_ = (t) => ({ transform: t.transform, patternId: `pattern-${t.rfId}` });
function jg({
  id: t,
  variant: r = Fn.Dots,
  // only used for dots and cross
  gap: i = 20,
  // only used for lines and cross
  size: l,
  lineWidth: u = 1,
  offset: c = 0,
  color: f,
  bgColor: h,
  style: p,
  className: m,
  patternClassName: v
}) {
  const g = q.useRef(null), { transform: y, patternId: x } = Me(f_, $e), S = l || c_[r], M = r === Fn.Dots, N = r === Fn.Cross, k = Array.isArray(i) ? i : [i, i], $ = [k[0] * y[2] || 1, k[1] * y[2] || 1], _ = S * y[2], E = Array.isArray(c) ? c : [c, c], D = N ? [_, _] : $, j = [
    E[0] * y[2] || 1 + D[0] / 2,
    E[1] * y[2] || 1 + D[1] / 2
  ], H = `${x}${t || ""}`;
  return I.jsxs("svg", { className: Ye(["react-flow__background", m]), style: {
    ...p,
    ...El,
    "--xy-background-color-props": h,
    "--xy-background-pattern-color-props": f
  }, ref: g, "data-testid": "rf__background", children: [I.jsx("pattern", { id: H, x: y[0] % $[0], y: y[1] % $[1], width: $[0], height: $[1], patternUnits: "userSpaceOnUse", patternTransform: `translate(-${j[0]},-${j[1]})`, children: M ? I.jsx(a_, { radius: _ / 2, className: v }) : I.jsx(u_, { dimensions: D, lineWidth: u, variant: r, className: v }) }), I.jsx("rect", { x: "0", y: "0", width: "100%", height: "100%", fill: `url(#${H})` })] });
}
jg.displayName = "Background";
const d_ = q.memo(jg);
function h_() {
  return I.jsx("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 32 32", children: I.jsx("path", { d: "M32 18.133H18.133V32h-4.266V18.133H0v-4.266h13.867V0h4.266v13.867H32z" }) });
}
function p_() {
  return I.jsx("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 32 5", children: I.jsx("path", { d: "M0 0h32v4.2H0z" }) });
}
function g_() {
  return I.jsx("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 32 30", children: I.jsx("path", { d: "M3.692 4.63c0-.53.4-.938.939-.938h5.215V0H4.708C2.13 0 0 2.054 0 4.63v5.216h3.692V4.631zM27.354 0h-5.2v3.692h5.17c.53 0 .984.4.984.939v5.215H32V4.631A4.624 4.624 0 0027.354 0zm.954 24.83c0 .532-.4.94-.939.94h-5.215v3.768h5.215c2.577 0 4.631-2.13 4.631-4.707v-5.139h-3.692v5.139zm-23.677.94c-.531 0-.939-.4-.939-.94v-5.138H0v5.139c0 2.577 2.13 4.707 4.708 4.707h5.138V25.77H4.631z" }) });
}
function m_() {
  return I.jsx("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 25 32", children: I.jsx("path", { d: "M21.333 10.667H19.81V7.619C19.81 3.429 16.38 0 12.19 0 8 0 4.571 3.429 4.571 7.619v3.048H3.048A3.056 3.056 0 000 13.714v15.238A3.056 3.056 0 003.048 32h18.285a3.056 3.056 0 003.048-3.048V13.714a3.056 3.056 0 00-3.048-3.047zM12.19 24.533a3.056 3.056 0 01-3.047-3.047 3.056 3.056 0 013.047-3.048 3.056 3.056 0 013.048 3.048 3.056 3.056 0 01-3.048 3.047zm4.724-13.866H7.467V7.619c0-2.59 2.133-4.724 4.723-4.724 2.591 0 4.724 2.133 4.724 4.724v3.048z" }) });
}
function y_() {
  return I.jsx("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 25 32", children: I.jsx("path", { d: "M21.333 10.667H19.81V7.619C19.81 3.429 16.38 0 12.19 0c-4.114 1.828-1.37 2.133.305 2.438 1.676.305 4.42 2.59 4.42 5.181v3.048H3.047A3.056 3.056 0 000 13.714v15.238A3.056 3.056 0 003.048 32h18.285a3.056 3.056 0 003.048-3.048V13.714a3.056 3.056 0 00-3.048-3.047zM12.19 24.533a3.056 3.056 0 01-3.047-3.047 3.056 3.056 0 013.047-3.048 3.056 3.056 0 013.048 3.048 3.056 3.056 0 01-3.048 3.047z" }) });
}
function bs({ children: t, className: r, ...i }) {
  return I.jsx("button", { type: "button", className: Ye(["react-flow__controls-button", r]), ...i, children: t });
}
const v_ = (t) => ({
  isInteractive: t.nodesDraggable || t.nodesConnectable || t.elementsSelectable,
  minZoomReached: t.transform[2] <= t.minZoom,
  maxZoomReached: t.transform[2] >= t.maxZoom,
  ariaLabelConfig: t.ariaLabelConfig
});
function Og({ style: t, showZoom: r = !0, showFitView: i = !0, showInteractive: l = !0, fitViewOptions: u, onZoomIn: c, onZoomOut: f, onFitView: h, onInteractiveChange: p, className: m, children: v, position: g = "bottom-left", orientation: y = "vertical", "aria-label": x }) {
  const S = je(), { isInteractive: M, minZoomReached: N, maxZoomReached: k, ariaLabelConfig: $ } = Me(v_, $e), { zoomIn: _, zoomOut: E, fitView: D } = _l(), j = () => {
    _(), c == null || c();
  }, H = () => {
    E(), f == null || f();
  }, b = () => {
    D(u), h == null || h();
  }, K = () => {
    S.setState({
      nodesDraggable: !M,
      nodesConnectable: !M,
      elementsSelectable: !M
    }), p == null || p(!M);
  }, ee = y === "horizontal" ? "horizontal" : "vertical";
  return I.jsxs(Sl, { className: Ye(["react-flow__controls", ee, m]), position: g, style: t, "data-testid": "rf__controls", "aria-label": x ?? $["controls.ariaLabel"], children: [r && I.jsxs(I.Fragment, { children: [I.jsx(bs, { onClick: j, className: "react-flow__controls-zoomin", title: $["controls.zoomIn.ariaLabel"], "aria-label": $["controls.zoomIn.ariaLabel"], disabled: k, children: I.jsx(h_, {}) }), I.jsx(bs, { onClick: H, className: "react-flow__controls-zoomout", title: $["controls.zoomOut.ariaLabel"], "aria-label": $["controls.zoomOut.ariaLabel"], disabled: N, children: I.jsx(p_, {}) })] }), i && I.jsx(bs, { className: "react-flow__controls-fitview", onClick: b, title: $["controls.fitView.ariaLabel"], "aria-label": $["controls.fitView.ariaLabel"], children: I.jsx(g_, {}) }), l && I.jsx(bs, { className: "react-flow__controls-interactive", onClick: K, title: $["controls.interactive.ariaLabel"], "aria-label": $["controls.interactive.ariaLabel"], children: M ? I.jsx(y_, {}) : I.jsx(m_, {}) }), v] });
}
Og.displayName = "Controls";
const w_ = q.memo(Og);
function x_({ id: t, x: r, y: i, width: l, height: u, style: c, color: f, strokeColor: h, strokeWidth: p, className: m, borderRadius: v, shapeRendering: g, selected: y, onClick: x }) {
  const { background: S, backgroundColor: M } = c || {}, N = f || S || M;
  return I.jsx("rect", { className: Ye(["react-flow__minimap-node", { selected: y }, m]), x: r, y: i, rx: v, ry: v, width: l, height: u, style: {
    fill: N,
    stroke: h,
    strokeWidth: p
  }, shapeRendering: g, onClick: x ? (k) => x(k, t) : void 0 });
}
const S_ = q.memo(x_), __ = (t) => t.nodes.map((r) => r.id), Aa = (t) => t instanceof Function ? t : () => t;
function E_({
  nodeStrokeColor: t,
  nodeColor: r,
  nodeClassName: i = "",
  nodeBorderRadius: l = 5,
  nodeStrokeWidth: u,
  /*
   * We need to rename the prop to be `CapitalCase` so that JSX will render it as
   * a component properly.
   */
  nodeComponent: c = S_,
  onClick: f
}) {
  const h = Me(__, $e), p = Aa(r), m = Aa(t), v = Aa(i), g = typeof window > "u" || window.chrome ? "crispEdges" : "geometricPrecision";
  return I.jsx(I.Fragment, { children: h.map((y) => (
    /*
     * The split of responsibilities between MiniMapNodes and
     * NodeComponentWrapper may appear weird. However, it’s designed to
     * minimize the cost of updates when individual nodes change.
     *
     * For more details, see a similar commit in `NodeRenderer/index.tsx`.
     */
    I.jsx(N_, { id: y, nodeColorFunc: p, nodeStrokeColorFunc: m, nodeClassNameFunc: v, nodeBorderRadius: l, nodeStrokeWidth: u, NodeComponent: c, onClick: f, shapeRendering: g }, y)
  )) });
}
function k_({ id: t, nodeColorFunc: r, nodeStrokeColorFunc: i, nodeClassNameFunc: l, nodeBorderRadius: u, nodeStrokeWidth: c, shapeRendering: f, NodeComponent: h, onClick: p }) {
  const { node: m, x: v, y: g, width: y, height: x } = Me((S) => {
    const M = S.nodeLookup.get(t);
    if (!M)
      return { node: void 0, x: 0, y: 0, width: 0, height: 0 };
    const N = M.internals.userNode, { x: k, y: $ } = M.internals.positionAbsolute, { width: _, height: E } = hn(N);
    return {
      node: N,
      x: k,
      y: $,
      width: _,
      height: E
    };
  }, $e);
  return !m || m.hidden || !Hp(m) ? null : I.jsx(h, { x: v, y: g, width: y, height: x, style: m.style, selected: !!m.selected, className: l(m), color: r(m), borderRadius: u, strokeColor: i(m), strokeWidth: c, shapeRendering: f, onClick: p, id: m.id });
}
const N_ = q.memo(k_);
var C_ = q.memo(E_);
const M_ = 200, P_ = 150, I_ = (t) => !t.hidden, z_ = (t) => {
  const r = {
    x: -t.transform[0] / t.transform[2],
    y: -t.transform[1] / t.transform[2],
    width: t.width / t.transform[2],
    height: t.height / t.transform[2]
  };
  return {
    viewBB: r,
    boundingRect: t.nodeLookup.size > 0 ? Fp(gi(t.nodeLookup, { filter: I_ }), r) : r,
    rfId: t.rfId,
    panZoom: t.panZoom,
    translateExtent: t.translateExtent,
    flowWidth: t.width,
    flowHeight: t.height,
    ariaLabelConfig: t.ariaLabelConfig
  };
}, T_ = "react-flow__minimap-desc";
function Fg({
  style: t,
  className: r,
  nodeStrokeColor: i,
  nodeColor: l,
  nodeClassName: u = "",
  nodeBorderRadius: c = 5,
  nodeStrokeWidth: f,
  /*
   * We need to rename the prop to be `CapitalCase` so that JSX will render it as
   * a component properly.
   */
  nodeComponent: h,
  bgColor: p,
  maskColor: m,
  maskStrokeColor: v,
  maskStrokeWidth: g,
  position: y = "bottom-right",
  onClick: x,
  onNodeClick: S,
  pannable: M = !1,
  zoomable: N = !1,
  ariaLabel: k,
  inversePan: $,
  zoomStep: _ = 1,
  offsetScale: E = 5
}) {
  const D = je(), j = q.useRef(null), { boundingRect: H, viewBB: b, rfId: K, panZoom: ee, translateExtent: U, flowWidth: G, flowHeight: Q, ariaLabelConfig: z } = Me(z_, $e), B = (t == null ? void 0 : t.width) ?? M_, F = (t == null ? void 0 : t.height) ?? P_, W = H.width / B, T = H.height / F, L = Math.max(W, T), V = L * B, C = L * F, A = E * L, te = H.x - (V - H.width) / 2 - A, re = H.y - (C - H.height) / 2 - A, le = V + A * 2, ue = C + A * 2, ae = `${T_}-${K}`, J = q.useRef(0), ce = q.useRef();
  J.current = L, q.useEffect(() => {
    if (j.current && ee)
      return ce.current = bx({
        domNode: j.current,
        panZoom: ee,
        getTransform: () => D.getState().transform,
        getViewScale: () => J.current
      }), () => {
        var ye;
        (ye = ce.current) == null || ye.destroy();
      };
  }, [ee]), q.useEffect(() => {
    var ye;
    (ye = ce.current) == null || ye.update({
      translateExtent: U,
      width: G,
      height: Q,
      inversePan: $,
      pannable: M,
      zoomStep: _,
      zoomable: N
    });
  }, [M, N, $, _, U, G, Q]);
  const xe = x ? (ye) => {
    var Ce;
    const [Ne, Te] = ((Ce = ce.current) == null ? void 0 : Ce.pointer(ye)) || [0, 0];
    x(ye, { x: Ne, y: Te });
  } : void 0, _e = S ? q.useCallback((ye, Ne) => {
    const Te = D.getState().nodeLookup.get(Ne).internals.userNode;
    S(ye, Te);
  }, []) : void 0, Se = k ?? z["minimap.ariaLabel"];
  return I.jsx(Sl, { position: y, style: {
    ...t,
    "--xy-minimap-background-color-props": typeof p == "string" ? p : void 0,
    "--xy-minimap-mask-background-color-props": typeof m == "string" ? m : void 0,
    "--xy-minimap-mask-stroke-color-props": typeof v == "string" ? v : void 0,
    "--xy-minimap-mask-stroke-width-props": typeof g == "number" ? g * L : void 0,
    "--xy-minimap-node-background-color-props": typeof l == "string" ? l : void 0,
    "--xy-minimap-node-stroke-color-props": typeof i == "string" ? i : void 0,
    "--xy-minimap-node-stroke-width-props": typeof f == "number" ? f : void 0
  }, className: Ye(["react-flow__minimap", r]), "data-testid": "rf__minimap", children: I.jsxs("svg", { width: B, height: F, viewBox: `${te} ${re} ${le} ${ue}`, className: "react-flow__minimap-svg", role: "img", "aria-labelledby": ae, ref: j, onClick: xe, children: [Se && I.jsx("title", { id: ae, children: Se }), I.jsx(C_, { onClick: _e, nodeColor: l, nodeStrokeColor: i, nodeBorderRadius: c, nodeClassName: u, nodeStrokeWidth: f, nodeComponent: h }), I.jsx("path", { className: "react-flow__minimap-mask", d: `M${te - A},${re - A}h${le + A * 2}v${ue + A * 2}h${-le - A * 2}z
        M${b.x},${b.y}h${b.width}v${b.height}h${-b.width}z`, fillRule: "evenodd", pointerEvents: "none" })] }) });
}
Fg.displayName = "MiniMap";
q.memo(Fg);
const L_ = (t) => (r) => t ? `${Math.max(1 / r.transform[2], 1)}` : void 0, A_ = {
  [to.Line]: "right",
  [to.Handle]: "bottom-right"
};
function R_({ nodeId: t, position: r, variant: i = to.Handle, className: l, style: u = void 0, children: c, color: f, minWidth: h = 10, minHeight: p = 10, maxWidth: m = Number.MAX_VALUE, maxHeight: v = Number.MAX_VALUE, keepAspectRatio: g = !1, resizeDirection: y, autoScale: x = !0, shouldResize: S, onResizeStart: M, onResize: N, onResizeEnd: k }) {
  const $ = mg(), _ = typeof t == "string" ? t : $, E = je(), D = q.useRef(null), j = i === to.Handle, H = Me(q.useCallback(L_(j && x), [j, x]), $e), b = q.useRef(null), K = r ?? A_[i];
  q.useEffect(() => {
    if (!(!D.current || !_))
      return b.current || (b.current = s1({
        domNode: D.current,
        nodeId: _,
        getStoreItems: () => {
          const { nodeLookup: U, transform: G, snapGrid: Q, snapToGrid: z, nodeOrigin: B, domNode: F } = E.getState();
          return {
            nodeLookup: U,
            transform: G,
            snapGrid: Q,
            snapToGrid: z,
            nodeOrigin: B,
            paneDomNode: F
          };
        },
        onChange: (U, G) => {
          const { triggerNodeChanges: Q, nodeLookup: z, parentLookup: B, nodeOrigin: F } = E.getState(), W = [], T = { x: U.x, y: U.y }, L = z.get(_);
          if (L && L.expandParent && L.parentId) {
            const V = L.origin ?? F, C = U.width ?? L.measured.width ?? 0, A = U.height ?? L.measured.height ?? 0, te = {
              id: L.id,
              parentId: L.parentId,
              rect: {
                width: C,
                height: A,
                ...Vp({
                  x: U.x ?? L.position.x,
                  y: U.y ?? L.position.y
                }, { width: C, height: A }, L.parentId, z, V)
              }
            }, re = cc([te], z, B, F);
            W.push(...re), T.x = U.x ? Math.max(V[0] * C, U.x) : void 0, T.y = U.y ? Math.max(V[1] * A, U.y) : void 0;
          }
          if (T.x !== void 0 && T.y !== void 0) {
            const V = {
              id: _,
              type: "position",
              position: { ...T }
            };
            W.push(V);
          }
          if (U.width !== void 0 && U.height !== void 0) {
            const C = {
              id: _,
              type: "dimensions",
              resizing: !0,
              setAttributes: y ? y === "horizontal" ? "width" : "height" : !0,
              dimensions: {
                width: U.width,
                height: U.height
              }
            };
            W.push(C);
          }
          for (const V of G) {
            const C = {
              ...V,
              type: "position"
            };
            W.push(C);
          }
          Q(W);
        },
        onEnd: ({ width: U, height: G }) => {
          const Q = {
            id: _,
            type: "dimensions",
            resizing: !1,
            dimensions: {
              width: U,
              height: G
            }
          };
          E.getState().triggerNodeChanges([Q]);
        }
      })), b.current.update({
        controlPosition: K,
        boundaries: {
          minWidth: h,
          minHeight: p,
          maxWidth: m,
          maxHeight: v
        },
        keepAspectRatio: g,
        resizeDirection: y,
        onResizeStart: M,
        onResize: N,
        onResizeEnd: k,
        shouldResize: S
      }), () => {
        var U;
        (U = b.current) == null || U.destroy();
      };
  }, [
    K,
    h,
    p,
    m,
    v,
    g,
    M,
    N,
    k,
    S
  ]);
  const ee = K.split("-");
  return I.jsx("div", { className: Ye(["react-flow__resize-control", "nodrag", ...ee, i, l]), ref: D, style: {
    ...u,
    scale: H,
    ...f && { [j ? "backgroundColor" : "borderColor"]: f }
  }, children: c });
}
q.memo(R_);
const $_ = 5200, cl = 1250, Zh = 2, Xs = 2, D_ = 0.5, qh = /* @__PURE__ */ new WeakMap(), j_ = {
  teamOrgNode: W_
}, O_ = {
  messagePulse: V_
};
function F_({ payload: t }) {
  return t ? /* @__PURE__ */ I.jsx(Dg, { children: /* @__PURE__ */ I.jsx(H_, { payload: t }) }) : null;
}
function H_({ payload: t }) {
  const r = _l(), { zoom: i } = l_(), l = q.useRef(null), u = q.useRef(null), c = q.useRef(/* @__PURE__ */ new Map()), f = q.useRef(/* @__PURE__ */ new Set()), h = q.useRef([]), [p, m] = q.useState({}), [v, g] = q.useState([]), [y, x] = q.useState(null), [S, M] = q.useState(""), N = B_(), k = t.messageEvents || [], $ = q.useMemo(
    () => t.nodes.map((E) => {
      if (E.data.kind !== "agent")
        return E;
      const D = i >= D_ ? p[E.data.agentId] : void 0;
      return D ? { ...E, data: { ...E.data, activityBubble: D } } : E;
    }),
    [p, t.nodes, i]
  ), _ = q.useMemo(
    () => [
      ...t.edges,
      ...v.map((E) => {
        const D = t.edges.find(
          (H) => H.source === E.sourceNodeId && H.target === E.targetNodeId || H.source === E.targetNodeId && H.target === E.sourceNodeId
        ), j = !!(D && D.source === E.targetNodeId && D.target === E.sourceNodeId);
        return {
          id: `message-pulse:${E.id}`,
          source: (D == null ? void 0 : D.source) || E.sourceNodeId,
          target: (D == null ? void 0 : D.target) || E.targetNodeId,
          sourceHandle: D == null ? void 0 : D.sourceHandle,
          targetHandle: D == null ? void 0 : D.targetHandle,
          type: "messagePulse",
          className: `org-flow-message-edge org-flow-message-edge--${E.itemType}`,
          data: { ...E, reverse: j },
          selectable: !1,
          focusable: !1,
          deletable: !1,
          zIndex: 20
        };
      })
    ],
    [v, t.edges]
  );
  return q.useEffect(() => {
    const E = /* @__PURE__ */ new Map();
    t.nodes.forEach((Q) => {
      Q.data.kind === "agent" && E.set(
        Q.data.agentId,
        `${Q.data.activity.tone}\0${Q.data.activity.headline}`
      );
    });
    const D = new Set(k.map((Q) => Q.id));
    if (u.current !== t.fitKey) {
      u.current = t.fitKey, c.current = E, f.current = D, m({}), g([]), x(null);
      return;
    }
    const H = t.nodes.filter((Q) => Q.data.kind === "agent").flatMap((Q) => {
      const z = E.get(Q.data.agentId) || "", B = c.current.get(Q.data.agentId);
      return !B || B === z ? [] : [{
        agentId: Q.data.agentId,
        label: Q.data.label,
        headline: Q.data.activity.headline,
        tone: Q.data.activity.tone,
        eventKey: `${Q.data.agentId}:${z}`
      }];
    });
    c.current = E;
    const b = k.filter(
      (Q) => !f.current.has(Q.id)
    );
    k.forEach((Q) => f.current.add(Q.id));
    const K = H.slice(0, Zh);
    K.length && (m((Q) => {
      const z = { ...Q };
      return K.forEach((B) => {
        z[B.agentId] = {
          headline: B.headline,
          tone: B.tone,
          eventKey: B.eventKey
        };
      }), z;
    }), K.forEach((Q) => {
      const z = window.setTimeout(() => {
        m((B) => {
          var W;
          if (((W = B[Q.agentId]) == null ? void 0 : W.eventKey) !== Q.eventKey)
            return B;
          const F = { ...B };
          return delete F[Q.agentId], F;
        });
      }, $_);
      h.current.push(z);
    }));
    const ee = N ? [] : b.slice(0, Xs);
    ee.length && (g((Q) => [...Q, ...ee].slice(-Xs)), ee.forEach((Q) => {
      const z = window.setTimeout(() => {
        g((B) => B.filter((F) => F.id !== Q.id));
      }, cl);
      h.current.push(z);
    }));
    const U = Math.max(0, H.length - Zh) + Math.max(0, b.length - Xs);
    if (U) {
      const Q = `${Date.now()}:${U}`;
      x({ eventKey: Q, count: U });
      const z = window.setTimeout(() => {
        x((B) => (B == null ? void 0 : B.eventKey) === Q ? null : B);
      }, cl);
      h.current.push(z);
    }
    const G = [
      ...K.map((Q) => `${Q.label}: ${Q.headline}`),
      ...b.slice(0, Xs).map((Q) => `${Q.sourceLabel} sent ${Q.label} to ${Q.targetLabel}`)
    ];
    U && G.push(`${U} more updates`), G.length && M(G.join(". "));
  }, [k, t.fitKey, t.nodes, N]), q.useEffect(() => () => {
    h.current.forEach((E) => window.clearTimeout(E));
  }, []), q.useEffect(() => {
    const E = l.current;
    if (!E)
      return;
    const D = (H) => {
      const b = q_(E, H);
      b && Vg(H, b);
    }, j = (H) => {
      const b = H.target;
      b instanceof Element && b.closest(".react-flow__node, .react-flow__controls, .react-flow__panel") || fl("team-org-flow:canvas-menu", H);
    };
    return E.addEventListener("wheel", D, { capture: !0, passive: !1 }), E.addEventListener("contextmenu", j, { capture: !0 }), () => {
      E.removeEventListener("wheel", D, { capture: !0 }), E.removeEventListener("contextmenu", j, { capture: !0 });
    };
  }, []), q.useEffect(() => {
    const E = window.requestAnimationFrame(() => {
      r.fitView({ padding: 0.18, duration: 180 });
    });
    return () => window.cancelAnimationFrame(E);
  }, [t.fitKey, r]), /* @__PURE__ */ I.jsxs("div", { ref: l, className: "team-org-flow-canvas", "data-team-org-flow-canvas": !0, children: [
    /* @__PURE__ */ I.jsxs(
      i_,
      {
        nodes: $,
        edges: _,
        nodeTypes: j_,
        edgeTypes: O_,
        nodesDraggable: !1,
        nodesConnectable: !1,
        elementsSelectable: !0,
        selectNodesOnDrag: !1,
        panOnScroll: !0,
        zoomOnScroll: !0,
        zoomOnPinch: !0,
        onPaneContextMenu: (E) => {
          E.preventDefault(), fl("team-org-flow:canvas-menu", E);
        },
        onMoveStart: () => {
          window.dispatchEvent(new CustomEvent("team-org-flow:dismiss-menu"));
        },
        fitView: !0,
        minZoom: 0.25,
        maxZoom: 1.5,
        defaultEdgeOptions: {
          type: "smoothstep",
          markerEnd: { type: li.ArrowClosed }
        },
        proOptions: { hideAttribution: !0 },
        children: [
          /* @__PURE__ */ I.jsx(d_, { gap: 24, size: 1 }),
          /* @__PURE__ */ I.jsx(w_, { position: "bottom-left", showInteractive: !1 })
        ]
      }
    ),
    y ? /* @__PURE__ */ I.jsxs("div", { className: "org-flow-overflow-notice", "aria-hidden": "true", children: [
      "+",
      y.count,
      " more update",
      y.count === 1 ? "" : "s"
    ] }) : null,
    /* @__PURE__ */ I.jsx("div", { className: "visually-hidden", "aria-live": "polite", "aria-atomic": "true", children: S })
  ] });
}
function V_({
  id: t,
  sourceX: r,
  sourceY: i,
  sourcePosition: l,
  targetX: u,
  targetY: c,
  targetPosition: f,
  data: h
}) {
  const [p] = ul({
    sourceX: r,
    sourceY: i,
    sourcePosition: l,
    targetX: u,
    targetY: c,
    targetPosition: f
  }), m = !!(h != null && h.reverse);
  return /* @__PURE__ */ I.jsxs("g", { className: "org-flow-message-pulse", "aria-hidden": "true", children: [
    /* @__PURE__ */ I.jsx(vi, { id: t, path: p, className: "org-flow-message-guide" }),
    /* @__PURE__ */ I.jsxs("circle", { className: "org-flow-message-particle", r: "5", children: [
      /* @__PURE__ */ I.jsx(
        "animateMotion",
        {
          dur: `${cl}ms`,
          path: p,
          keyPoints: m ? "1;0" : "0;1",
          keyTimes: "0;1",
          calcMode: "linear",
          fill: "freeze"
        }
      ),
      /* @__PURE__ */ I.jsx(
        "animate",
        {
          attributeName: "opacity",
          values: "0;1;1;0",
          keyTimes: "0;0.12;0.72;1",
          dur: `${cl}ms`,
          fill: "freeze"
        }
      )
    ] })
  ] });
}
function B_() {
  const [t, r] = q.useState(!1);
  return q.useEffect(() => {
    var u;
    const i = window.matchMedia("(prefers-reduced-motion: reduce)"), l = () => r(i.matches);
    return l(), (u = i.addEventListener) == null || u.call(i, "change", l), () => {
      var c;
      return (c = i.removeEventListener) == null ? void 0 : c.call(i, "change", l);
    };
  }, []), t;
}
function W_({ data: t }) {
  let r;
  return t.kind === "agent" ? r = /* @__PURE__ */ I.jsx(U_, { data: t }) : t.kind === "team" ? r = /* @__PURE__ */ I.jsx(Y_, { data: t }) : t.kind === "governance" ? r = /* @__PURE__ */ I.jsx(b_, { data: t }) : r = /* @__PURE__ */ I.jsx(X_, { data: t }), /* @__PURE__ */ I.jsxs("div", { className: `team-org-flow-node team-org-flow-node--${t.kind}`, children: [
    /* @__PURE__ */ I.jsx(
      no,
      {
        className: "team-org-flow-handle team-org-flow-handle--target",
        type: "target",
        position: me.Top,
        isConnectable: !1
      }
    ),
    r,
    /* @__PURE__ */ I.jsx(
      no,
      {
        className: "team-org-flow-handle team-org-flow-handle--source",
        type: "source",
        position: me.Bottom,
        isConnectable: !1
      }
    )
  ] });
}
function U_({ data: t }) {
  const r = q.useId(), i = () => {
    window.dispatchEvent(
      new CustomEvent("team-org-flow:edit-agent", {
        detail: { agentId: t.agentId }
      })
    );
  };
  return /* @__PURE__ */ I.jsxs(
    "article",
    {
      className: t.cardClassName,
      tabIndex: 0,
      role: "button",
      "aria-label": `Edit ${t.label}`,
      "aria-describedby": r,
      onClick: i,
      onContextMenu: (l) => {
        fl("team-org-flow:agent-menu", l, { agentId: t.agentId });
      },
      onKeyDown: (l) => {
        l.key !== "Enter" && l.key !== " " || (l.preventDefault(), i());
      },
      children: [
        /* @__PURE__ */ I.jsxs("div", { className: "hierarchy-node-summary", children: [
          /* @__PURE__ */ I.jsxs("div", { className: "hierarchy-node-title", children: [
            /* @__PURE__ */ I.jsx(
              Q_,
              {
                avatar: t.avatar,
                label: t.label,
                role: t.role,
                idLabel: t.idLabel
              }
            ),
            /* @__PURE__ */ I.jsx("strong", { children: t.label })
          ] }),
          /* @__PURE__ */ I.jsxs("div", { className: "hierarchy-node-subtitle", children: [
            /* @__PURE__ */ I.jsx("span", { className: "hierarchy-node-role", children: t.role }),
            /* @__PURE__ */ I.jsx("span", { className: `state-pill ${t.status}`, children: t.status }),
            /* @__PURE__ */ I.jsx(K_, { awake: t.awake }),
            /* @__PURE__ */ I.jsx(G_, { indicators: t.inboxIndicators })
          ] })
        ] }),
        /* @__PURE__ */ I.jsxs("span", { id: r, className: "visually-hidden", children: [
          "Agent ID ",
          t.agentId,
          ". Role ",
          t.role,
          "."
        ] }),
        /* @__PURE__ */ I.jsxs("div", { className: "org-node-work", children: [
          /* @__PURE__ */ I.jsxs("div", { className: "org-node-work-line org-node-work-line--now", children: [
            /* @__PURE__ */ I.jsx("span", { children: "Now" }),
            /* @__PURE__ */ I.jsx("strong", { children: t.work.primary })
          ] }),
          /* @__PURE__ */ I.jsxs("div", { className: "org-node-work-line org-node-work-line--waiting", children: [
            /* @__PURE__ */ I.jsx("span", { children: "Waiting" }),
            /* @__PURE__ */ I.jsx("strong", { children: t.work.waiting })
          ] })
        ] }),
        t.activityBubble ? /* @__PURE__ */ I.jsxs(
          "span",
          {
            className: `org-flow-activity-bubble org-flow-activity-bubble--${t.activityBubble.tone || "idle"}`,
            "aria-hidden": "true",
            children: [
              /* @__PURE__ */ I.jsx("small", { children: "Activity" }),
              /* @__PURE__ */ I.jsx("strong", { children: t.activityBubble.headline })
            ]
          }
        ) : null,
        /* @__PURE__ */ I.jsx("div", { className: "hierarchy-node-details org-node-hover-panel", children: t.detailLines.map((l, u) => /* @__PURE__ */ I.jsx("div", { className: "meta-line", children: l }, `${l}-${u}`)) })
      ]
    }
  );
}
function Y_({ data: t }) {
  return /* @__PURE__ */ I.jsxs("article", { className: "org-focus-team org-flow-team-node", children: [
    /* @__PURE__ */ I.jsxs("div", { className: "org-flow-team-header", children: [
      /* @__PURE__ */ I.jsxs("div", { className: "org-flow-team-title", children: [
        /* @__PURE__ */ I.jsx("p", { className: "section-label", children: t.teamId }),
        /* @__PURE__ */ I.jsx("h3", { children: t.name })
      ] }),
      /* @__PURE__ */ I.jsxs("div", { className: "org-flow-node-pills org-focus-team-status", children: [
        t.archived ? /* @__PURE__ */ I.jsx("span", { className: "state-pill archived", children: "archived" }) : null,
        t.costBlocked ? /* @__PURE__ */ I.jsx("span", { className: "state-pill warning team-cost-blocked-pill", children: "cost blocked" }) : null,
        /* @__PURE__ */ I.jsxs("span", { className: "state-pill info", children: [
          t.awakeCount,
          " awake"
        ] }),
        /* @__PURE__ */ I.jsxs("span", { className: `state-pill ${t.attentionCount ? "warning" : "accepted"}`, children: [
          t.attentionCount,
          " attention"
        ] })
      ] })
    ] }),
    /* @__PURE__ */ I.jsx(Qa, { html: t.costHtml }),
    /* @__PURE__ */ I.jsx(Qa, { html: t.costBlockedNoticeHtml })
  ] });
}
function b_({ data: t }) {
  return /* @__PURE__ */ I.jsxs(
    "article",
    {
      className: "org-flow-governance-node governance-item",
      "data-governance-group-id": t.groupId,
      onContextMenu: (r) => {
        fl("team-org-flow:governance-menu", r, { groupId: t.groupId });
      },
      children: [
        /* @__PURE__ */ I.jsxs("div", { className: "org-flow-governance-header", children: [
          /* @__PURE__ */ I.jsxs("div", { className: "org-flow-governance-title", children: [
            /* @__PURE__ */ I.jsx("p", { className: "section-label", children: "Governance" }),
            /* @__PURE__ */ I.jsx("h3", { children: t.name })
          ] }),
          /* @__PURE__ */ I.jsxs("div", { className: "org-flow-node-pills", children: [
            t.archived ? /* @__PURE__ */ I.jsx("span", { className: "state-pill blocked", children: "archived" }) : null,
            t.costAttention ? /* @__PURE__ */ I.jsx("span", { className: "state-pill warning", children: "cost attention" }) : null,
            /* @__PURE__ */ I.jsxs("span", { className: "state-pill neutral", children: [
              t.memberTeamCount,
              " team",
              t.memberTeamCount === 1 ? "" : "s"
            ] }),
            /* @__PURE__ */ I.jsxs("span", { className: `state-pill ${t.pendingAgendaCount ? "warning" : "accepted"}`, children: [
              t.pendingAgendaCount,
              " agenda"
            ] })
          ] }),
          /* @__PURE__ */ I.jsxs("div", { className: "org-flow-governance-manager", children: [
            t.managerAvatar ? /* @__PURE__ */ I.jsx(Hg, { avatar: t.managerAvatar, extraClass: "agent-avatar-small" }) : null,
            /* @__PURE__ */ I.jsx("span", { children: t.managerLabel })
          ] })
        ] }),
        /* @__PURE__ */ I.jsx("div", { className: "meta-line", children: t.groupId }),
        /* @__PURE__ */ I.jsx(Qa, { html: t.costHtml })
      ]
    }
  );
}
function X_({ data: t }) {
  return /* @__PURE__ */ I.jsxs("article", { className: "hierarchy-node-card hierarchy-node-card--warning org-flow-warning-node", tabIndex: 0, children: [
    /* @__PURE__ */ I.jsxs("div", { className: "hierarchy-node-summary", children: [
      /* @__PURE__ */ I.jsx("div", { className: "hierarchy-node-title", children: /* @__PURE__ */ I.jsx("strong", { children: t.label }) }),
      /* @__PURE__ */ I.jsx("div", { className: "hierarchy-node-subtitle", children: /* @__PURE__ */ I.jsx("span", { className: "state-pill warning", children: "warning" }) })
    ] }),
    t.detailLines.length ? /* @__PURE__ */ I.jsx("div", { className: "hierarchy-node-details", children: t.detailLines.map((r, i) => /* @__PURE__ */ I.jsx("div", { className: "meta-line", children: r }, `${r}-${i}`)) }) : null
  ] });
}
function Hg({
  avatar: t,
  extraClass: r = ""
}) {
  const i = ["agent-avatar", r].filter(Boolean).join(" ");
  return t.src ? /* @__PURE__ */ I.jsx("span", { className: i, children: /* @__PURE__ */ I.jsx("img", { src: t.src, alt: t.alt, loading: "lazy" }) }) : /* @__PURE__ */ I.jsx("span", { className: `${i} agent-avatar-fallback`, "aria-hidden": "true", children: t.initials });
}
function Q_({
  avatar: t,
  label: r,
  role: i,
  idLabel: l
}) {
  return /* @__PURE__ */ I.jsxs("span", { className: "hierarchy-node-avatar-wrap", children: [
    /* @__PURE__ */ I.jsx(Hg, { avatar: t }),
    /* @__PURE__ */ I.jsxs("span", { className: "hierarchy-node-avatar-card", "aria-hidden": "true", children: [
      /* @__PURE__ */ I.jsx("strong", { className: "hierarchy-node-avatar-name", children: r }),
      l ? /* @__PURE__ */ I.jsx("span", { className: "hierarchy-node-avatar-id", children: l }) : null,
      /* @__PURE__ */ I.jsx("span", { className: "hierarchy-node-avatar-role", children: i })
    ] })
  ] });
}
function K_({ awake: t }) {
  return /* @__PURE__ */ I.jsxs("span", { className: `awake-indicator awake-indicator--${t.kind}`, title: t.detail, children: [
    /* @__PURE__ */ I.jsx("span", { className: "awake-dot", "aria-hidden": "true" }),
    t.label
  ] });
}
function G_({ indicators: t }) {
  return /* @__PURE__ */ I.jsx("span", { className: "agent-inbox-indicators", "aria-label": "Pending inbox summary", children: t.map((r) => /* @__PURE__ */ I.jsx(Z_, { indicator: r }, r.itemType)) });
}
function Z_({ indicator: t }) {
  const r = q.useId(), i = q.useRef(null), l = t.items || [], u = l.length > 0;
  return q.useEffect(() => {
    const c = i.current;
    if (!c)
      return;
    const f = (h) => {
      Vg(h, c);
    };
    return c.addEventListener("wheel", f, { passive: !1 }), () => {
      c.removeEventListener("wheel", f);
    };
  }, [u]), /* @__PURE__ */ I.jsxs(
    "span",
    {
      className: [
        "agent-inbox-indicator",
        `agent-inbox-indicator--${t.itemType}`,
        t.count ? "agent-inbox-indicator--active" : "agent-inbox-indicator--empty"
      ].join(" "),
      "aria-label": `${t.label}: ${t.count}`,
      "aria-describedby": u ? r : void 0,
      tabIndex: u ? 0 : void 0,
      onClick: (c) => c.stopPropagation(),
      onKeyDown: (c) => c.stopPropagation(),
      children: [
        /* @__PURE__ */ I.jsx(J_, { direction: t.direction }),
        /* @__PURE__ */ I.jsx("strong", { children: t.count }),
        u ? /* @__PURE__ */ I.jsx(
          "span",
          {
            ref: i,
            className: "agent-inbox-popover nowheel",
            role: "tooltip",
            id: r,
            "aria-label": t.label,
            children: l.map((c, f) => /* @__PURE__ */ I.jsxs("span", { className: "agent-inbox-bubble", children: [
              /* @__PURE__ */ I.jsx("strong", { children: c.detail }),
              c.meta ? /* @__PURE__ */ I.jsx("small", { children: c.meta }) : null
            ] }, `${c.detail}-${c.meta}-${f}`))
          }
        ) : null
      ]
    }
  );
}
function q_(t, r) {
  for (const u of Array.from(t.querySelectorAll(".agent-inbox-popover"))) {
    const c = u.getBoundingClientRect();
    if (c.width > 0 && c.height > 0 && r.clientX >= c.left && r.clientX <= c.right && r.clientY >= c.top && r.clientY <= c.bottom)
      return u;
  }
  const i = document.elementFromPoint(r.clientX, r.clientY), l = i == null ? void 0 : i.closest(".agent-inbox-popover");
  return l instanceof HTMLElement && t.contains(l) ? l : null;
}
function Vg(t, r) {
  r.scrollTop += t.deltaY, t.preventDefault(), t.stopPropagation(), t.stopImmediatePropagation();
}
function J_({ direction: t }) {
  return /* @__PURE__ */ I.jsxs("svg", { className: `agent-inbox-icon agent-inbox-icon--${t}`, viewBox: "0 0 20 20", "aria-hidden": "true", focusable: "false", children: [
    t === "up" ? /* @__PURE__ */ I.jsxs(I.Fragment, { children: [
      /* @__PURE__ */ I.jsx("path", { d: "M10 8V3" }),
      /* @__PURE__ */ I.jsx("path", { d: "M7.3 5.7 10 3l2.7 2.7" })
    ] }) : /* @__PURE__ */ I.jsxs(I.Fragment, { children: [
      /* @__PURE__ */ I.jsx("path", { d: "M10 3v5" }),
      /* @__PURE__ */ I.jsx("path", { d: "M7.3 5.3 10 8l2.7-2.7" })
    ] }),
    /* @__PURE__ */ I.jsx("path", { d: "M5.5 8.2h9a2.2 2.2 0 0 1 2.2 2.2v2.6a2.2 2.2 0 0 1-2.2 2.2H9.3L5 18v-2.8a2.2 2.2 0 0 1-1.7-2.1v-2.7a2.2 2.2 0 0 1 2.2-2.2Z" })
  ] });
}
function Qa({ html: t }) {
  return t ? /* @__PURE__ */ I.jsx("div", { dangerouslySetInnerHTML: { __html: t } }) : null;
}
function fl(t, r, i = {}) {
  r.preventDefault(), r.stopPropagation(), window.dispatchEvent(
    new CustomEvent(t, {
      detail: {
        ...i,
        clientX: r.clientX,
        clientY: r.clientY
      }
    })
  );
}
function dc(t) {
  const r = document.querySelector("#team-org-flow-root");
  if (!r) {
    window.__TEAM_ORG_FLOW_PAYLOAD__ = t;
    return;
  }
  let i = qh.get(r);
  i || (i = P0.createRoot(r), qh.set(r, i)), i.render(/* @__PURE__ */ I.jsx(F_, { payload: t }));
}
window.renderTeamOrgFlow = dc;
window.addEventListener("team-org-flow:render", (t) => {
  dc(t.detail ?? null);
});
window.__TEAM_ORG_FLOW_PAYLOAD__ && dc(window.__TEAM_ORG_FLOW_PAYLOAD__);
