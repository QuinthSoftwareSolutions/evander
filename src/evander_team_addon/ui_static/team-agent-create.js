var rf = Object.defineProperty;
var lf = (c, g, a) => g in c ? rf(c, g, { enumerable: !0, configurable: !0, writable: !0, value: a }) : c[g] = a;
var To = (c, g, a) => lf(c, typeof g != "symbol" ? g + "" : g, a);
var Lo = { exports: {} }, kr = {}, Ro = { exports: {} }, ne = {};
/**
 * @license React
 * react.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var Ia;
function of() {
  if (Ia) return ne;
  Ia = 1;
  var c = Symbol.for("react.element"), g = Symbol.for("react.portal"), a = Symbol.for("react.fragment"), P = Symbol.for("react.strict_mode"), S = Symbol.for("react.profiler"), D = Symbol.for("react.provider"), F = Symbol.for("react.context"), B = Symbol.for("react.forward_ref"), _ = Symbol.for("react.suspense"), M = Symbol.for("react.memo"), k = Symbol.for("react.lazy"), L = Symbol.iterator;
  function w(m) {
    return m === null || typeof m != "object" ? null : (m = L && m[L] || m["@@iterator"], typeof m == "function" ? m : null);
  }
  var E = { isMounted: function() {
    return !1;
  }, enqueueForceUpdate: function() {
  }, enqueueReplaceState: function() {
  }, enqueueSetState: function() {
  } }, R = Object.assign, Y = {};
  function $(m, f, x) {
    this.props = m, this.context = f, this.refs = Y, this.updater = x || E;
  }
  $.prototype.isReactComponent = {}, $.prototype.setState = function(m, f) {
    if (typeof m != "object" && typeof m != "function" && m != null) throw Error("setState(...): takes an object of state variables to update or a function which returns an object of state variables.");
    this.updater.enqueueSetState(this, m, f, "setState");
  }, $.prototype.forceUpdate = function(m) {
    this.updater.enqueueForceUpdate(this, m, "forceUpdate");
  };
  function ae() {
  }
  ae.prototype = $.prototype;
  function ge(m, f, x) {
    this.props = m, this.context = f, this.refs = Y, this.updater = x || E;
  }
  var _e = ge.prototype = new ae();
  _e.constructor = ge, R(_e, $.prototype), _e.isPureReactComponent = !0;
  var X = Array.isArray, J = Object.prototype.hasOwnProperty, b = { current: null }, Z = { key: !0, ref: !0, __self: !0, __source: !0 };
  function ee(m, f, x) {
    var W, re = {}, ie = null, pe = null;
    if (f != null) for (W in f.ref !== void 0 && (pe = f.ref), f.key !== void 0 && (ie = "" + f.key), f) J.call(f, W) && !Z.hasOwnProperty(W) && (re[W] = f[W]);
    var ce = arguments.length - 2;
    if (ce === 1) re.children = x;
    else if (1 < ce) {
      for (var ye = Array(ce), et = 0; et < ce; et++) ye[et] = arguments[et + 2];
      re.children = ye;
    }
    if (m && m.defaultProps) for (W in ce = m.defaultProps, ce) re[W] === void 0 && (re[W] = ce[W]);
    return { $$typeof: c, type: m, key: ie, ref: pe, props: re, _owner: b.current };
  }
  function xe(m, f) {
    return { $$typeof: c, type: m.type, key: f, ref: m.ref, props: m.props, _owner: m._owner };
  }
  function fe(m) {
    return typeof m == "object" && m !== null && m.$$typeof === c;
  }
  function Oe(m) {
    var f = { "=": "=0", ":": "=2" };
    return "$" + m.replace(/[=:]/g, function(x) {
      return f[x];
    });
  }
  var Te = /\/+/g;
  function je(m, f) {
    return typeof m == "object" && m !== null && m.key != null ? Oe("" + m.key) : f.toString(36);
  }
  function Ae(m, f, x, W, re) {
    var ie = typeof m;
    (ie === "undefined" || ie === "boolean") && (m = null);
    var pe = !1;
    if (m === null) pe = !0;
    else switch (ie) {
      case "string":
      case "number":
        pe = !0;
        break;
      case "object":
        switch (m.$$typeof) {
          case c:
          case g:
            pe = !0;
        }
    }
    if (pe) return pe = m, re = re(pe), m = W === "" ? "." + je(pe, 0) : W, X(re) ? (x = "", m != null && (x = m.replace(Te, "$&/") + "/"), Ae(re, f, x, "", function(et) {
      return et;
    })) : re != null && (fe(re) && (re = xe(re, x + (!re.key || pe && pe.key === re.key ? "" : ("" + re.key).replace(Te, "$&/") + "/") + m)), f.push(re)), 1;
    if (pe = 0, W = W === "" ? "." : W + ":", X(m)) for (var ce = 0; ce < m.length; ce++) {
      ie = m[ce];
      var ye = W + je(ie, ce);
      pe += Ae(ie, f, x, ye, re);
    }
    else if (ye = w(m), typeof ye == "function") for (m = ye.call(m), ce = 0; !(ie = m.next()).done; ) ie = ie.value, ye = W + je(ie, ce++), pe += Ae(ie, f, x, ye, re);
    else if (ie === "object") throw f = String(m), Error("Objects are not valid as a React child (found: " + (f === "[object Object]" ? "object with keys {" + Object.keys(m).join(", ") + "}" : f) + "). If you meant to render a collection of children, use an array instead.");
    return pe;
  }
  function We(m, f, x) {
    if (m == null) return m;
    var W = [], re = 0;
    return Ae(m, W, "", "", function(ie) {
      return f.call(x, ie, re++);
    }), W;
  }
  function Le(m) {
    if (m._status === -1) {
      var f = m._result;
      f = f(), f.then(function(x) {
        (m._status === 0 || m._status === -1) && (m._status = 1, m._result = x);
      }, function(x) {
        (m._status === 0 || m._status === -1) && (m._status = 2, m._result = x);
      }), m._status === -1 && (m._status = 0, m._result = f);
    }
    if (m._status === 1) return m._result.default;
    throw m._result;
  }
  var ue = { current: null }, z = { transition: null }, Q = { ReactCurrentDispatcher: ue, ReactCurrentBatchConfig: z, ReactCurrentOwner: b };
  function O() {
    throw Error("act(...) is not supported in production builds of React.");
  }
  return ne.Children = { map: We, forEach: function(m, f, x) {
    We(m, function() {
      f.apply(this, arguments);
    }, x);
  }, count: function(m) {
    var f = 0;
    return We(m, function() {
      f++;
    }), f;
  }, toArray: function(m) {
    return We(m, function(f) {
      return f;
    }) || [];
  }, only: function(m) {
    if (!fe(m)) throw Error("React.Children.only expected to receive a single React element child.");
    return m;
  } }, ne.Component = $, ne.Fragment = a, ne.Profiler = S, ne.PureComponent = ge, ne.StrictMode = P, ne.Suspense = _, ne.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED = Q, ne.act = O, ne.cloneElement = function(m, f, x) {
    if (m == null) throw Error("React.cloneElement(...): The argument must be a React element, but you passed " + m + ".");
    var W = R({}, m.props), re = m.key, ie = m.ref, pe = m._owner;
    if (f != null) {
      if (f.ref !== void 0 && (ie = f.ref, pe = b.current), f.key !== void 0 && (re = "" + f.key), m.type && m.type.defaultProps) var ce = m.type.defaultProps;
      for (ye in f) J.call(f, ye) && !Z.hasOwnProperty(ye) && (W[ye] = f[ye] === void 0 && ce !== void 0 ? ce[ye] : f[ye]);
    }
    var ye = arguments.length - 2;
    if (ye === 1) W.children = x;
    else if (1 < ye) {
      ce = Array(ye);
      for (var et = 0; et < ye; et++) ce[et] = arguments[et + 2];
      W.children = ce;
    }
    return { $$typeof: c, type: m.type, key: re, ref: ie, props: W, _owner: pe };
  }, ne.createContext = function(m) {
    return m = { $$typeof: F, _currentValue: m, _currentValue2: m, _threadCount: 0, Provider: null, Consumer: null, _defaultValue: null, _globalName: null }, m.Provider = { $$typeof: D, _context: m }, m.Consumer = m;
  }, ne.createElement = ee, ne.createFactory = function(m) {
    var f = ee.bind(null, m);
    return f.type = m, f;
  }, ne.createRef = function() {
    return { current: null };
  }, ne.forwardRef = function(m) {
    return { $$typeof: B, render: m };
  }, ne.isValidElement = fe, ne.lazy = function(m) {
    return { $$typeof: k, _payload: { _status: -1, _result: m }, _init: Le };
  }, ne.memo = function(m, f) {
    return { $$typeof: M, type: m, compare: f === void 0 ? null : f };
  }, ne.startTransition = function(m) {
    var f = z.transition;
    z.transition = {};
    try {
      m();
    } finally {
      z.transition = f;
    }
  }, ne.unstable_act = O, ne.useCallback = function(m, f) {
    return ue.current.useCallback(m, f);
  }, ne.useContext = function(m) {
    return ue.current.useContext(m);
  }, ne.useDebugValue = function() {
  }, ne.useDeferredValue = function(m) {
    return ue.current.useDeferredValue(m);
  }, ne.useEffect = function(m, f) {
    return ue.current.useEffect(m, f);
  }, ne.useId = function() {
    return ue.current.useId();
  }, ne.useImperativeHandle = function(m, f, x) {
    return ue.current.useImperativeHandle(m, f, x);
  }, ne.useInsertionEffect = function(m, f) {
    return ue.current.useInsertionEffect(m, f);
  }, ne.useLayoutEffect = function(m, f) {
    return ue.current.useLayoutEffect(m, f);
  }, ne.useMemo = function(m, f) {
    return ue.current.useMemo(m, f);
  }, ne.useReducer = function(m, f, x) {
    return ue.current.useReducer(m, f, x);
  }, ne.useRef = function(m) {
    return ue.current.useRef(m);
  }, ne.useState = function(m) {
    return ue.current.useState(m);
  }, ne.useSyncExternalStore = function(m, f, x) {
    return ue.current.useSyncExternalStore(m, f, x);
  }, ne.useTransition = function() {
    return ue.current.useTransition();
  }, ne.version = "18.3.1", ne;
}
var Da;
function Fo() {
  return Da || (Da = 1, Ro.exports = of()), Ro.exports;
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
var Oa;
function sf() {
  if (Oa) return kr;
  Oa = 1;
  var c = Fo(), g = Symbol.for("react.element"), a = Symbol.for("react.fragment"), P = Object.prototype.hasOwnProperty, S = c.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED.ReactCurrentOwner, D = { key: !0, ref: !0, __self: !0, __source: !0 };
  function F(B, _, M) {
    var k, L = {}, w = null, E = null;
    M !== void 0 && (w = "" + M), _.key !== void 0 && (w = "" + _.key), _.ref !== void 0 && (E = _.ref);
    for (k in _) P.call(_, k) && !D.hasOwnProperty(k) && (L[k] = _[k]);
    if (B && B.defaultProps) for (k in _ = B.defaultProps, _) L[k] === void 0 && (L[k] = _[k]);
    return { $$typeof: g, type: B, key: w, ref: E, props: L, _owner: S.current };
  }
  return kr.Fragment = a, kr.jsx = F, kr.jsxs = F, kr;
}
var Aa;
function uf() {
  return Aa || (Aa = 1, Lo.exports = sf()), Lo.exports;
}
var s = uf(), te = Fo(), zl = {}, Mo = { exports: {} }, be = {}, zo = { exports: {} }, Io = {};
/**
 * @license React
 * scheduler.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var Fa;
function af() {
  return Fa || (Fa = 1, (function(c) {
    function g(z, Q) {
      var O = z.length;
      z.push(Q);
      e: for (; 0 < O; ) {
        var m = O - 1 >>> 1, f = z[m];
        if (0 < S(f, Q)) z[m] = Q, z[O] = f, O = m;
        else break e;
      }
    }
    function a(z) {
      return z.length === 0 ? null : z[0];
    }
    function P(z) {
      if (z.length === 0) return null;
      var Q = z[0], O = z.pop();
      if (O !== Q) {
        z[0] = O;
        e: for (var m = 0, f = z.length, x = f >>> 1; m < x; ) {
          var W = 2 * (m + 1) - 1, re = z[W], ie = W + 1, pe = z[ie];
          if (0 > S(re, O)) ie < f && 0 > S(pe, re) ? (z[m] = pe, z[ie] = O, m = ie) : (z[m] = re, z[W] = O, m = W);
          else if (ie < f && 0 > S(pe, O)) z[m] = pe, z[ie] = O, m = ie;
          else break e;
        }
      }
      return Q;
    }
    function S(z, Q) {
      var O = z.sortIndex - Q.sortIndex;
      return O !== 0 ? O : z.id - Q.id;
    }
    if (typeof performance == "object" && typeof performance.now == "function") {
      var D = performance;
      c.unstable_now = function() {
        return D.now();
      };
    } else {
      var F = Date, B = F.now();
      c.unstable_now = function() {
        return F.now() - B;
      };
    }
    var _ = [], M = [], k = 1, L = null, w = 3, E = !1, R = !1, Y = !1, $ = typeof setTimeout == "function" ? setTimeout : null, ae = typeof clearTimeout == "function" ? clearTimeout : null, ge = typeof setImmediate < "u" ? setImmediate : null;
    typeof navigator < "u" && navigator.scheduling !== void 0 && navigator.scheduling.isInputPending !== void 0 && navigator.scheduling.isInputPending.bind(navigator.scheduling);
    function _e(z) {
      for (var Q = a(M); Q !== null; ) {
        if (Q.callback === null) P(M);
        else if (Q.startTime <= z) P(M), Q.sortIndex = Q.expirationTime, g(_, Q);
        else break;
        Q = a(M);
      }
    }
    function X(z) {
      if (Y = !1, _e(z), !R) if (a(_) !== null) R = !0, Le(J);
      else {
        var Q = a(M);
        Q !== null && ue(X, Q.startTime - z);
      }
    }
    function J(z, Q) {
      R = !1, Y && (Y = !1, ae(ee), ee = -1), E = !0;
      var O = w;
      try {
        for (_e(Q), L = a(_); L !== null && (!(L.expirationTime > Q) || z && !Oe()); ) {
          var m = L.callback;
          if (typeof m == "function") {
            L.callback = null, w = L.priorityLevel;
            var f = m(L.expirationTime <= Q);
            Q = c.unstable_now(), typeof f == "function" ? L.callback = f : L === a(_) && P(_), _e(Q);
          } else P(_);
          L = a(_);
        }
        if (L !== null) var x = !0;
        else {
          var W = a(M);
          W !== null && ue(X, W.startTime - Q), x = !1;
        }
        return x;
      } finally {
        L = null, w = O, E = !1;
      }
    }
    var b = !1, Z = null, ee = -1, xe = 5, fe = -1;
    function Oe() {
      return !(c.unstable_now() - fe < xe);
    }
    function Te() {
      if (Z !== null) {
        var z = c.unstable_now();
        fe = z;
        var Q = !0;
        try {
          Q = Z(!0, z);
        } finally {
          Q ? je() : (b = !1, Z = null);
        }
      } else b = !1;
    }
    var je;
    if (typeof ge == "function") je = function() {
      ge(Te);
    };
    else if (typeof MessageChannel < "u") {
      var Ae = new MessageChannel(), We = Ae.port2;
      Ae.port1.onmessage = Te, je = function() {
        We.postMessage(null);
      };
    } else je = function() {
      $(Te, 0);
    };
    function Le(z) {
      Z = z, b || (b = !0, je());
    }
    function ue(z, Q) {
      ee = $(function() {
        z(c.unstable_now());
      }, Q);
    }
    c.unstable_IdlePriority = 5, c.unstable_ImmediatePriority = 1, c.unstable_LowPriority = 4, c.unstable_NormalPriority = 3, c.unstable_Profiling = null, c.unstable_UserBlockingPriority = 2, c.unstable_cancelCallback = function(z) {
      z.callback = null;
    }, c.unstable_continueExecution = function() {
      R || E || (R = !0, Le(J));
    }, c.unstable_forceFrameRate = function(z) {
      0 > z || 125 < z ? console.error("forceFrameRate takes a positive int between 0 and 125, forcing frame rates higher than 125 fps is not supported") : xe = 0 < z ? Math.floor(1e3 / z) : 5;
    }, c.unstable_getCurrentPriorityLevel = function() {
      return w;
    }, c.unstable_getFirstCallbackNode = function() {
      return a(_);
    }, c.unstable_next = function(z) {
      switch (w) {
        case 1:
        case 2:
        case 3:
          var Q = 3;
          break;
        default:
          Q = w;
      }
      var O = w;
      w = Q;
      try {
        return z();
      } finally {
        w = O;
      }
    }, c.unstable_pauseExecution = function() {
    }, c.unstable_requestPaint = function() {
    }, c.unstable_runWithPriority = function(z, Q) {
      switch (z) {
        case 1:
        case 2:
        case 3:
        case 4:
        case 5:
          break;
        default:
          z = 3;
      }
      var O = w;
      w = z;
      try {
        return Q();
      } finally {
        w = O;
      }
    }, c.unstable_scheduleCallback = function(z, Q, O) {
      var m = c.unstable_now();
      switch (typeof O == "object" && O !== null ? (O = O.delay, O = typeof O == "number" && 0 < O ? m + O : m) : O = m, z) {
        case 1:
          var f = -1;
          break;
        case 2:
          f = 250;
          break;
        case 5:
          f = 1073741823;
          break;
        case 4:
          f = 1e4;
          break;
        default:
          f = 5e3;
      }
      return f = O + f, z = { id: k++, callback: Q, priorityLevel: z, startTime: O, expirationTime: f, sortIndex: -1 }, O > m ? (z.sortIndex = O, g(M, z), a(_) === null && z === a(M) && (Y ? (ae(ee), ee = -1) : Y = !0, ue(X, O - m))) : (z.sortIndex = f, g(_, z), R || E || (R = !0, Le(J))), z;
    }, c.unstable_shouldYield = Oe, c.unstable_wrapCallback = function(z) {
      var Q = w;
      return function() {
        var O = w;
        w = Q;
        try {
          return z.apply(this, arguments);
        } finally {
          w = O;
        }
      };
    };
  })(Io)), Io;
}
var Ua;
function cf() {
  return Ua || (Ua = 1, zo.exports = af()), zo.exports;
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
var Va;
function df() {
  if (Va) return be;
  Va = 1;
  var c = Fo(), g = cf();
  function a(e) {
    for (var t = "https://reactjs.org/docs/error-decoder.html?invariant=" + e, n = 1; n < arguments.length; n++) t += "&args[]=" + encodeURIComponent(arguments[n]);
    return "Minified React error #" + e + "; visit " + t + " for the full message or use the non-minified dev environment for full errors and additional helpful warnings.";
  }
  var P = /* @__PURE__ */ new Set(), S = {};
  function D(e, t) {
    F(e, t), F(e + "Capture", t);
  }
  function F(e, t) {
    for (S[e] = t, e = 0; e < t.length; e++) P.add(t[e]);
  }
  var B = !(typeof window > "u" || typeof window.document > "u" || typeof window.document.createElement > "u"), _ = Object.prototype.hasOwnProperty, M = /^[:A-Z_a-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF\u0370-\u037D\u037F-\u1FFF\u200C-\u200D\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD][:A-Z_a-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF\u0370-\u037D\u037F-\u1FFF\u200C-\u200D\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD\-.0-9\u00B7\u0300-\u036F\u203F-\u2040]*$/, k = {}, L = {};
  function w(e) {
    return _.call(L, e) ? !0 : _.call(k, e) ? !1 : M.test(e) ? L[e] = !0 : (k[e] = !0, !1);
  }
  function E(e, t, n, r) {
    if (n !== null && n.type === 0) return !1;
    switch (typeof t) {
      case "function":
      case "symbol":
        return !0;
      case "boolean":
        return r ? !1 : n !== null ? !n.acceptsBooleans : (e = e.toLowerCase().slice(0, 5), e !== "data-" && e !== "aria-");
      default:
        return !1;
    }
  }
  function R(e, t, n, r) {
    if (t === null || typeof t > "u" || E(e, t, n, r)) return !0;
    if (r) return !1;
    if (n !== null) switch (n.type) {
      case 3:
        return !t;
      case 4:
        return t === !1;
      case 5:
        return isNaN(t);
      case 6:
        return isNaN(t) || 1 > t;
    }
    return !1;
  }
  function Y(e, t, n, r, l, i, o) {
    this.acceptsBooleans = t === 2 || t === 3 || t === 4, this.attributeName = r, this.attributeNamespace = l, this.mustUseProperty = n, this.propertyName = e, this.type = t, this.sanitizeURL = i, this.removeEmptyString = o;
  }
  var $ = {};
  "children dangerouslySetInnerHTML defaultValue defaultChecked innerHTML suppressContentEditableWarning suppressHydrationWarning style".split(" ").forEach(function(e) {
    $[e] = new Y(e, 0, !1, e, null, !1, !1);
  }), [["acceptCharset", "accept-charset"], ["className", "class"], ["htmlFor", "for"], ["httpEquiv", "http-equiv"]].forEach(function(e) {
    var t = e[0];
    $[t] = new Y(t, 1, !1, e[1], null, !1, !1);
  }), ["contentEditable", "draggable", "spellCheck", "value"].forEach(function(e) {
    $[e] = new Y(e, 2, !1, e.toLowerCase(), null, !1, !1);
  }), ["autoReverse", "externalResourcesRequired", "focusable", "preserveAlpha"].forEach(function(e) {
    $[e] = new Y(e, 2, !1, e, null, !1, !1);
  }), "allowFullScreen async autoFocus autoPlay controls default defer disabled disablePictureInPicture disableRemotePlayback formNoValidate hidden loop noModule noValidate open playsInline readOnly required reversed scoped seamless itemScope".split(" ").forEach(function(e) {
    $[e] = new Y(e, 3, !1, e.toLowerCase(), null, !1, !1);
  }), ["checked", "multiple", "muted", "selected"].forEach(function(e) {
    $[e] = new Y(e, 3, !0, e, null, !1, !1);
  }), ["capture", "download"].forEach(function(e) {
    $[e] = new Y(e, 4, !1, e, null, !1, !1);
  }), ["cols", "rows", "size", "span"].forEach(function(e) {
    $[e] = new Y(e, 6, !1, e, null, !1, !1);
  }), ["rowSpan", "start"].forEach(function(e) {
    $[e] = new Y(e, 5, !1, e.toLowerCase(), null, !1, !1);
  });
  var ae = /[\-:]([a-z])/g;
  function ge(e) {
    return e[1].toUpperCase();
  }
  "accent-height alignment-baseline arabic-form baseline-shift cap-height clip-path clip-rule color-interpolation color-interpolation-filters color-profile color-rendering dominant-baseline enable-background fill-opacity fill-rule flood-color flood-opacity font-family font-size font-size-adjust font-stretch font-style font-variant font-weight glyph-name glyph-orientation-horizontal glyph-orientation-vertical horiz-adv-x horiz-origin-x image-rendering letter-spacing lighting-color marker-end marker-mid marker-start overline-position overline-thickness paint-order panose-1 pointer-events rendering-intent shape-rendering stop-color stop-opacity strikethrough-position strikethrough-thickness stroke-dasharray stroke-dashoffset stroke-linecap stroke-linejoin stroke-miterlimit stroke-opacity stroke-width text-anchor text-decoration text-rendering underline-position underline-thickness unicode-bidi unicode-range units-per-em v-alphabetic v-hanging v-ideographic v-mathematical vector-effect vert-adv-y vert-origin-x vert-origin-y word-spacing writing-mode xmlns:xlink x-height".split(" ").forEach(function(e) {
    var t = e.replace(
      ae,
      ge
    );
    $[t] = new Y(t, 1, !1, e, null, !1, !1);
  }), "xlink:actuate xlink:arcrole xlink:role xlink:show xlink:title xlink:type".split(" ").forEach(function(e) {
    var t = e.replace(ae, ge);
    $[t] = new Y(t, 1, !1, e, "http://www.w3.org/1999/xlink", !1, !1);
  }), ["xml:base", "xml:lang", "xml:space"].forEach(function(e) {
    var t = e.replace(ae, ge);
    $[t] = new Y(t, 1, !1, e, "http://www.w3.org/XML/1998/namespace", !1, !1);
  }), ["tabIndex", "crossOrigin"].forEach(function(e) {
    $[e] = new Y(e, 1, !1, e.toLowerCase(), null, !1, !1);
  }), $.xlinkHref = new Y("xlinkHref", 1, !1, "xlink:href", "http://www.w3.org/1999/xlink", !0, !1), ["src", "href", "action", "formAction"].forEach(function(e) {
    $[e] = new Y(e, 1, !1, e.toLowerCase(), null, !0, !0);
  });
  function _e(e, t, n, r) {
    var l = $.hasOwnProperty(t) ? $[t] : null;
    (l !== null ? l.type !== 0 : r || !(2 < t.length) || t[0] !== "o" && t[0] !== "O" || t[1] !== "n" && t[1] !== "N") && (R(t, n, l, r) && (n = null), r || l === null ? w(t) && (n === null ? e.removeAttribute(t) : e.setAttribute(t, "" + n)) : l.mustUseProperty ? e[l.propertyName] = n === null ? l.type === 3 ? !1 : "" : n : (t = l.attributeName, r = l.attributeNamespace, n === null ? e.removeAttribute(t) : (l = l.type, n = l === 3 || l === 4 && n === !0 ? "" : "" + n, r ? e.setAttributeNS(r, t, n) : e.setAttribute(t, n))));
  }
  var X = c.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED, J = Symbol.for("react.element"), b = Symbol.for("react.portal"), Z = Symbol.for("react.fragment"), ee = Symbol.for("react.strict_mode"), xe = Symbol.for("react.profiler"), fe = Symbol.for("react.provider"), Oe = Symbol.for("react.context"), Te = Symbol.for("react.forward_ref"), je = Symbol.for("react.suspense"), Ae = Symbol.for("react.suspense_list"), We = Symbol.for("react.memo"), Le = Symbol.for("react.lazy"), ue = Symbol.for("react.offscreen"), z = Symbol.iterator;
  function Q(e) {
    return e === null || typeof e != "object" ? null : (e = z && e[z] || e["@@iterator"], typeof e == "function" ? e : null);
  }
  var O = Object.assign, m;
  function f(e) {
    if (m === void 0) try {
      throw Error();
    } catch (n) {
      var t = n.stack.trim().match(/\n( *(at )?)/);
      m = t && t[1] || "";
    }
    return `
` + m + e;
  }
  var x = !1;
  function W(e, t) {
    if (!e || x) return "";
    x = !0;
    var n = Error.prepareStackTrace;
    Error.prepareStackTrace = void 0;
    try {
      if (t) if (t = function() {
        throw Error();
      }, Object.defineProperty(t.prototype, "props", { set: function() {
        throw Error();
      } }), typeof Reflect == "object" && Reflect.construct) {
        try {
          Reflect.construct(t, []);
        } catch (y) {
          var r = y;
        }
        Reflect.construct(e, [], t);
      } else {
        try {
          t.call();
        } catch (y) {
          r = y;
        }
        e.call(t.prototype);
      }
      else {
        try {
          throw Error();
        } catch (y) {
          r = y;
        }
        e();
      }
    } catch (y) {
      if (y && r && typeof y.stack == "string") {
        for (var l = y.stack.split(`
`), i = r.stack.split(`
`), o = l.length - 1, u = i.length - 1; 1 <= o && 0 <= u && l[o] !== i[u]; ) u--;
        for (; 1 <= o && 0 <= u; o--, u--) if (l[o] !== i[u]) {
          if (o !== 1 || u !== 1)
            do
              if (o--, u--, 0 > u || l[o] !== i[u]) {
                var d = `
` + l[o].replace(" at new ", " at ");
                return e.displayName && d.includes("<anonymous>") && (d = d.replace("<anonymous>", e.displayName)), d;
              }
            while (1 <= o && 0 <= u);
          break;
        }
      }
    } finally {
      x = !1, Error.prepareStackTrace = n;
    }
    return (e = e ? e.displayName || e.name : "") ? f(e) : "";
  }
  function re(e) {
    switch (e.tag) {
      case 5:
        return f(e.type);
      case 16:
        return f("Lazy");
      case 13:
        return f("Suspense");
      case 19:
        return f("SuspenseList");
      case 0:
      case 2:
      case 15:
        return e = W(e.type, !1), e;
      case 11:
        return e = W(e.type.render, !1), e;
      case 1:
        return e = W(e.type, !0), e;
      default:
        return "";
    }
  }
  function ie(e) {
    if (e == null) return null;
    if (typeof e == "function") return e.displayName || e.name || null;
    if (typeof e == "string") return e;
    switch (e) {
      case Z:
        return "Fragment";
      case b:
        return "Portal";
      case xe:
        return "Profiler";
      case ee:
        return "StrictMode";
      case je:
        return "Suspense";
      case Ae:
        return "SuspenseList";
    }
    if (typeof e == "object") switch (e.$$typeof) {
      case Oe:
        return (e.displayName || "Context") + ".Consumer";
      case fe:
        return (e._context.displayName || "Context") + ".Provider";
      case Te:
        var t = e.render;
        return e = e.displayName, e || (e = t.displayName || t.name || "", e = e !== "" ? "ForwardRef(" + e + ")" : "ForwardRef"), e;
      case We:
        return t = e.displayName || null, t !== null ? t : ie(e.type) || "Memo";
      case Le:
        t = e._payload, e = e._init;
        try {
          return ie(e(t));
        } catch {
        }
    }
    return null;
  }
  function pe(e) {
    var t = e.type;
    switch (e.tag) {
      case 24:
        return "Cache";
      case 9:
        return (t.displayName || "Context") + ".Consumer";
      case 10:
        return (t._context.displayName || "Context") + ".Provider";
      case 18:
        return "DehydratedFragment";
      case 11:
        return e = t.render, e = e.displayName || e.name || "", t.displayName || (e !== "" ? "ForwardRef(" + e + ")" : "ForwardRef");
      case 7:
        return "Fragment";
      case 5:
        return t;
      case 4:
        return "Portal";
      case 3:
        return "Root";
      case 6:
        return "Text";
      case 16:
        return ie(t);
      case 8:
        return t === ee ? "StrictMode" : "Mode";
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
        if (typeof t == "function") return t.displayName || t.name || null;
        if (typeof t == "string") return t;
    }
    return null;
  }
  function ce(e) {
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
  function ye(e) {
    var t = e.type;
    return (e = e.nodeName) && e.toLowerCase() === "input" && (t === "checkbox" || t === "radio");
  }
  function et(e) {
    var t = ye(e) ? "checked" : "value", n = Object.getOwnPropertyDescriptor(e.constructor.prototype, t), r = "" + e[t];
    if (!e.hasOwnProperty(t) && typeof n < "u" && typeof n.get == "function" && typeof n.set == "function") {
      var l = n.get, i = n.set;
      return Object.defineProperty(e, t, { configurable: !0, get: function() {
        return l.call(this);
      }, set: function(o) {
        r = "" + o, i.call(this, o);
      } }), Object.defineProperty(e, t, { enumerable: n.enumerable }), { getValue: function() {
        return r;
      }, setValue: function(o) {
        r = "" + o;
      }, stopTracking: function() {
        e._valueTracker = null, delete e[t];
      } };
    }
  }
  function _r(e) {
    e._valueTracker || (e._valueTracker = et(e));
  }
  function Vo(e) {
    if (!e) return !1;
    var t = e._valueTracker;
    if (!t) return !0;
    var n = t.getValue(), r = "";
    return e && (r = ye(e) ? e.checked ? "true" : "false" : e.value), e = r, e !== n ? (t.setValue(e), !0) : !1;
  }
  function Cr(e) {
    if (e = e || (typeof document < "u" ? document : void 0), typeof e > "u") return null;
    try {
      return e.activeElement || e.body;
    } catch {
      return e.body;
    }
  }
  function Dl(e, t) {
    var n = t.checked;
    return O({}, t, { defaultChecked: void 0, defaultValue: void 0, value: void 0, checked: n ?? e._wrapperState.initialChecked });
  }
  function $o(e, t) {
    var n = t.defaultValue == null ? "" : t.defaultValue, r = t.checked != null ? t.checked : t.defaultChecked;
    n = ce(t.value != null ? t.value : n), e._wrapperState = { initialChecked: r, initialValue: n, controlled: t.type === "checkbox" || t.type === "radio" ? t.checked != null : t.value != null };
  }
  function Ho(e, t) {
    t = t.checked, t != null && _e(e, "checked", t, !1);
  }
  function Ol(e, t) {
    Ho(e, t);
    var n = ce(t.value), r = t.type;
    if (n != null) r === "number" ? (n === 0 && e.value === "" || e.value != n) && (e.value = "" + n) : e.value !== "" + n && (e.value = "" + n);
    else if (r === "submit" || r === "reset") {
      e.removeAttribute("value");
      return;
    }
    t.hasOwnProperty("value") ? Al(e, t.type, n) : t.hasOwnProperty("defaultValue") && Al(e, t.type, ce(t.defaultValue)), t.checked == null && t.defaultChecked != null && (e.defaultChecked = !!t.defaultChecked);
  }
  function Bo(e, t, n) {
    if (t.hasOwnProperty("value") || t.hasOwnProperty("defaultValue")) {
      var r = t.type;
      if (!(r !== "submit" && r !== "reset" || t.value !== void 0 && t.value !== null)) return;
      t = "" + e._wrapperState.initialValue, n || t === e.value || (e.value = t), e.defaultValue = t;
    }
    n = e.name, n !== "" && (e.name = ""), e.defaultChecked = !!e._wrapperState.initialChecked, n !== "" && (e.name = n);
  }
  function Al(e, t, n) {
    (t !== "number" || Cr(e.ownerDocument) !== e) && (n == null ? e.defaultValue = "" + e._wrapperState.initialValue : e.defaultValue !== "" + n && (e.defaultValue = "" + n));
  }
  var On = Array.isArray;
  function dn(e, t, n, r) {
    if (e = e.options, t) {
      t = {};
      for (var l = 0; l < n.length; l++) t["$" + n[l]] = !0;
      for (n = 0; n < e.length; n++) l = t.hasOwnProperty("$" + e[n].value), e[n].selected !== l && (e[n].selected = l), l && r && (e[n].defaultSelected = !0);
    } else {
      for (n = "" + ce(n), t = null, l = 0; l < e.length; l++) {
        if (e[l].value === n) {
          e[l].selected = !0, r && (e[l].defaultSelected = !0);
          return;
        }
        t !== null || e[l].disabled || (t = e[l]);
      }
      t !== null && (t.selected = !0);
    }
  }
  function Fl(e, t) {
    if (t.dangerouslySetInnerHTML != null) throw Error(a(91));
    return O({}, t, { value: void 0, defaultValue: void 0, children: "" + e._wrapperState.initialValue });
  }
  function Wo(e, t) {
    var n = t.value;
    if (n == null) {
      if (n = t.children, t = t.defaultValue, n != null) {
        if (t != null) throw Error(a(92));
        if (On(n)) {
          if (1 < n.length) throw Error(a(93));
          n = n[0];
        }
        t = n;
      }
      t == null && (t = ""), n = t;
    }
    e._wrapperState = { initialValue: ce(n) };
  }
  function Qo(e, t) {
    var n = ce(t.value), r = ce(t.defaultValue);
    n != null && (n = "" + n, n !== e.value && (e.value = n), t.defaultValue == null && e.defaultValue !== n && (e.defaultValue = n)), r != null && (e.defaultValue = "" + r);
  }
  function Go(e) {
    var t = e.textContent;
    t === e._wrapperState.initialValue && t !== "" && t !== null && (e.value = t);
  }
  function Ko(e) {
    switch (e) {
      case "svg":
        return "http://www.w3.org/2000/svg";
      case "math":
        return "http://www.w3.org/1998/Math/MathML";
      default:
        return "http://www.w3.org/1999/xhtml";
    }
  }
  function Ul(e, t) {
    return e == null || e === "http://www.w3.org/1999/xhtml" ? Ko(t) : e === "http://www.w3.org/2000/svg" && t === "foreignObject" ? "http://www.w3.org/1999/xhtml" : e;
  }
  var jr, Yo = (function(e) {
    return typeof MSApp < "u" && MSApp.execUnsafeLocalFunction ? function(t, n, r, l) {
      MSApp.execUnsafeLocalFunction(function() {
        return e(t, n, r, l);
      });
    } : e;
  })(function(e, t) {
    if (e.namespaceURI !== "http://www.w3.org/2000/svg" || "innerHTML" in e) e.innerHTML = t;
    else {
      for (jr = jr || document.createElement("div"), jr.innerHTML = "<svg>" + t.valueOf().toString() + "</svg>", t = jr.firstChild; e.firstChild; ) e.removeChild(e.firstChild);
      for (; t.firstChild; ) e.appendChild(t.firstChild);
    }
  });
  function An(e, t) {
    if (t) {
      var n = e.firstChild;
      if (n && n === e.lastChild && n.nodeType === 3) {
        n.nodeValue = t;
        return;
      }
    }
    e.textContent = t;
  }
  var Fn = {
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
  }, sc = ["Webkit", "ms", "Moz", "O"];
  Object.keys(Fn).forEach(function(e) {
    sc.forEach(function(t) {
      t = t + e.charAt(0).toUpperCase() + e.substring(1), Fn[t] = Fn[e];
    });
  });
  function qo(e, t, n) {
    return t == null || typeof t == "boolean" || t === "" ? "" : n || typeof t != "number" || t === 0 || Fn.hasOwnProperty(e) && Fn[e] ? ("" + t).trim() : t + "px";
  }
  function Xo(e, t) {
    e = e.style;
    for (var n in t) if (t.hasOwnProperty(n)) {
      var r = n.indexOf("--") === 0, l = qo(n, t[n], r);
      n === "float" && (n = "cssFloat"), r ? e.setProperty(n, l) : e[n] = l;
    }
  }
  var uc = O({ menuitem: !0 }, { area: !0, base: !0, br: !0, col: !0, embed: !0, hr: !0, img: !0, input: !0, keygen: !0, link: !0, meta: !0, param: !0, source: !0, track: !0, wbr: !0 });
  function Vl(e, t) {
    if (t) {
      if (uc[e] && (t.children != null || t.dangerouslySetInnerHTML != null)) throw Error(a(137, e));
      if (t.dangerouslySetInnerHTML != null) {
        if (t.children != null) throw Error(a(60));
        if (typeof t.dangerouslySetInnerHTML != "object" || !("__html" in t.dangerouslySetInnerHTML)) throw Error(a(61));
      }
      if (t.style != null && typeof t.style != "object") throw Error(a(62));
    }
  }
  function $l(e, t) {
    if (e.indexOf("-") === -1) return typeof t.is == "string";
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
  var Hl = null;
  function Bl(e) {
    return e = e.target || e.srcElement || window, e.correspondingUseElement && (e = e.correspondingUseElement), e.nodeType === 3 ? e.parentNode : e;
  }
  var Wl = null, fn = null, pn = null;
  function Zo(e) {
    if (e = or(e)) {
      if (typeof Wl != "function") throw Error(a(280));
      var t = e.stateNode;
      t && (t = Yr(t), Wl(e.stateNode, e.type, t));
    }
  }
  function Jo(e) {
    fn ? pn ? pn.push(e) : pn = [e] : fn = e;
  }
  function bo() {
    if (fn) {
      var e = fn, t = pn;
      if (pn = fn = null, Zo(e), t) for (e = 0; e < t.length; e++) Zo(t[e]);
    }
  }
  function es(e, t) {
    return e(t);
  }
  function ts() {
  }
  var Ql = !1;
  function ns(e, t, n) {
    if (Ql) return e(t, n);
    Ql = !0;
    try {
      return es(e, t, n);
    } finally {
      Ql = !1, (fn !== null || pn !== null) && (ts(), bo());
    }
  }
  function Un(e, t) {
    var n = e.stateNode;
    if (n === null) return null;
    var r = Yr(n);
    if (r === null) return null;
    n = r[t];
    e: switch (t) {
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
        (r = !r.disabled) || (e = e.type, r = !(e === "button" || e === "input" || e === "select" || e === "textarea")), e = !r;
        break e;
      default:
        e = !1;
    }
    if (e) return null;
    if (n && typeof n != "function") throw Error(a(231, t, typeof n));
    return n;
  }
  var Gl = !1;
  if (B) try {
    var Vn = {};
    Object.defineProperty(Vn, "passive", { get: function() {
      Gl = !0;
    } }), window.addEventListener("test", Vn, Vn), window.removeEventListener("test", Vn, Vn);
  } catch {
    Gl = !1;
  }
  function ac(e, t, n, r, l, i, o, u, d) {
    var y = Array.prototype.slice.call(arguments, 3);
    try {
      t.apply(n, y);
    } catch (j) {
      this.onError(j);
    }
  }
  var $n = !1, Er = null, Nr = !1, Kl = null, cc = { onError: function(e) {
    $n = !0, Er = e;
  } };
  function dc(e, t, n, r, l, i, o, u, d) {
    $n = !1, Er = null, ac.apply(cc, arguments);
  }
  function fc(e, t, n, r, l, i, o, u, d) {
    if (dc.apply(this, arguments), $n) {
      if ($n) {
        var y = Er;
        $n = !1, Er = null;
      } else throw Error(a(198));
      Nr || (Nr = !0, Kl = y);
    }
  }
  function Xt(e) {
    var t = e, n = e;
    if (e.alternate) for (; t.return; ) t = t.return;
    else {
      e = t;
      do
        t = e, (t.flags & 4098) !== 0 && (n = t.return), e = t.return;
      while (e);
    }
    return t.tag === 3 ? n : null;
  }
  function rs(e) {
    if (e.tag === 13) {
      var t = e.memoizedState;
      if (t === null && (e = e.alternate, e !== null && (t = e.memoizedState)), t !== null) return t.dehydrated;
    }
    return null;
  }
  function ls(e) {
    if (Xt(e) !== e) throw Error(a(188));
  }
  function pc(e) {
    var t = e.alternate;
    if (!t) {
      if (t = Xt(e), t === null) throw Error(a(188));
      return t !== e ? null : e;
    }
    for (var n = e, r = t; ; ) {
      var l = n.return;
      if (l === null) break;
      var i = l.alternate;
      if (i === null) {
        if (r = l.return, r !== null) {
          n = r;
          continue;
        }
        break;
      }
      if (l.child === i.child) {
        for (i = l.child; i; ) {
          if (i === n) return ls(l), e;
          if (i === r) return ls(l), t;
          i = i.sibling;
        }
        throw Error(a(188));
      }
      if (n.return !== r.return) n = l, r = i;
      else {
        for (var o = !1, u = l.child; u; ) {
          if (u === n) {
            o = !0, n = l, r = i;
            break;
          }
          if (u === r) {
            o = !0, r = l, n = i;
            break;
          }
          u = u.sibling;
        }
        if (!o) {
          for (u = i.child; u; ) {
            if (u === n) {
              o = !0, n = i, r = l;
              break;
            }
            if (u === r) {
              o = !0, r = i, n = l;
              break;
            }
            u = u.sibling;
          }
          if (!o) throw Error(a(189));
        }
      }
      if (n.alternate !== r) throw Error(a(190));
    }
    if (n.tag !== 3) throw Error(a(188));
    return n.stateNode.current === n ? e : t;
  }
  function is(e) {
    return e = pc(e), e !== null ? os(e) : null;
  }
  function os(e) {
    if (e.tag === 5 || e.tag === 6) return e;
    for (e = e.child; e !== null; ) {
      var t = os(e);
      if (t !== null) return t;
      e = e.sibling;
    }
    return null;
  }
  var ss = g.unstable_scheduleCallback, us = g.unstable_cancelCallback, mc = g.unstable_shouldYield, hc = g.unstable_requestPaint, Ee = g.unstable_now, vc = g.unstable_getCurrentPriorityLevel, Yl = g.unstable_ImmediatePriority, as = g.unstable_UserBlockingPriority, Pr = g.unstable_NormalPriority, gc = g.unstable_LowPriority, cs = g.unstable_IdlePriority, Tr = null, yt = null;
  function yc(e) {
    if (yt && typeof yt.onCommitFiberRoot == "function") try {
      yt.onCommitFiberRoot(Tr, e, void 0, (e.current.flags & 128) === 128);
    } catch {
    }
  }
  var dt = Math.clz32 ? Math.clz32 : kc, wc = Math.log, xc = Math.LN2;
  function kc(e) {
    return e >>>= 0, e === 0 ? 32 : 31 - (wc(e) / xc | 0) | 0;
  }
  var Lr = 64, Rr = 4194304;
  function Hn(e) {
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
  function Mr(e, t) {
    var n = e.pendingLanes;
    if (n === 0) return 0;
    var r = 0, l = e.suspendedLanes, i = e.pingedLanes, o = n & 268435455;
    if (o !== 0) {
      var u = o & ~l;
      u !== 0 ? r = Hn(u) : (i &= o, i !== 0 && (r = Hn(i)));
    } else o = n & ~l, o !== 0 ? r = Hn(o) : i !== 0 && (r = Hn(i));
    if (r === 0) return 0;
    if (t !== 0 && t !== r && (t & l) === 0 && (l = r & -r, i = t & -t, l >= i || l === 16 && (i & 4194240) !== 0)) return t;
    if ((r & 4) !== 0 && (r |= n & 16), t = e.entangledLanes, t !== 0) for (e = e.entanglements, t &= r; 0 < t; ) n = 31 - dt(t), l = 1 << n, r |= e[n], t &= ~l;
    return r;
  }
  function Sc(e, t) {
    switch (e) {
      case 1:
      case 2:
      case 4:
        return t + 250;
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
        return t + 5e3;
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
  function _c(e, t) {
    for (var n = e.suspendedLanes, r = e.pingedLanes, l = e.expirationTimes, i = e.pendingLanes; 0 < i; ) {
      var o = 31 - dt(i), u = 1 << o, d = l[o];
      d === -1 ? ((u & n) === 0 || (u & r) !== 0) && (l[o] = Sc(u, t)) : d <= t && (e.expiredLanes |= u), i &= ~u;
    }
  }
  function ql(e) {
    return e = e.pendingLanes & -1073741825, e !== 0 ? e : e & 1073741824 ? 1073741824 : 0;
  }
  function ds() {
    var e = Lr;
    return Lr <<= 1, (Lr & 4194240) === 0 && (Lr = 64), e;
  }
  function Xl(e) {
    for (var t = [], n = 0; 31 > n; n++) t.push(e);
    return t;
  }
  function Bn(e, t, n) {
    e.pendingLanes |= t, t !== 536870912 && (e.suspendedLanes = 0, e.pingedLanes = 0), e = e.eventTimes, t = 31 - dt(t), e[t] = n;
  }
  function Cc(e, t) {
    var n = e.pendingLanes & ~t;
    e.pendingLanes = t, e.suspendedLanes = 0, e.pingedLanes = 0, e.expiredLanes &= t, e.mutableReadLanes &= t, e.entangledLanes &= t, t = e.entanglements;
    var r = e.eventTimes;
    for (e = e.expirationTimes; 0 < n; ) {
      var l = 31 - dt(n), i = 1 << l;
      t[l] = 0, r[l] = -1, e[l] = -1, n &= ~i;
    }
  }
  function Zl(e, t) {
    var n = e.entangledLanes |= t;
    for (e = e.entanglements; n; ) {
      var r = 31 - dt(n), l = 1 << r;
      l & t | e[r] & t && (e[r] |= t), n &= ~l;
    }
  }
  var de = 0;
  function fs(e) {
    return e &= -e, 1 < e ? 4 < e ? (e & 268435455) !== 0 ? 16 : 536870912 : 4 : 1;
  }
  var ps, Jl, ms, hs, vs, bl = !1, zr = [], Rt = null, Mt = null, zt = null, Wn = /* @__PURE__ */ new Map(), Qn = /* @__PURE__ */ new Map(), It = [], jc = "mousedown mouseup touchcancel touchend touchstart auxclick dblclick pointercancel pointerdown pointerup dragend dragstart drop compositionend compositionstart keydown keypress keyup input textInput copy cut paste click change contextmenu reset submit".split(" ");
  function gs(e, t) {
    switch (e) {
      case "focusin":
      case "focusout":
        Rt = null;
        break;
      case "dragenter":
      case "dragleave":
        Mt = null;
        break;
      case "mouseover":
      case "mouseout":
        zt = null;
        break;
      case "pointerover":
      case "pointerout":
        Wn.delete(t.pointerId);
        break;
      case "gotpointercapture":
      case "lostpointercapture":
        Qn.delete(t.pointerId);
    }
  }
  function Gn(e, t, n, r, l, i) {
    return e === null || e.nativeEvent !== i ? (e = { blockedOn: t, domEventName: n, eventSystemFlags: r, nativeEvent: i, targetContainers: [l] }, t !== null && (t = or(t), t !== null && Jl(t)), e) : (e.eventSystemFlags |= r, t = e.targetContainers, l !== null && t.indexOf(l) === -1 && t.push(l), e);
  }
  function Ec(e, t, n, r, l) {
    switch (t) {
      case "focusin":
        return Rt = Gn(Rt, e, t, n, r, l), !0;
      case "dragenter":
        return Mt = Gn(Mt, e, t, n, r, l), !0;
      case "mouseover":
        return zt = Gn(zt, e, t, n, r, l), !0;
      case "pointerover":
        var i = l.pointerId;
        return Wn.set(i, Gn(Wn.get(i) || null, e, t, n, r, l)), !0;
      case "gotpointercapture":
        return i = l.pointerId, Qn.set(i, Gn(Qn.get(i) || null, e, t, n, r, l)), !0;
    }
    return !1;
  }
  function ys(e) {
    var t = Zt(e.target);
    if (t !== null) {
      var n = Xt(t);
      if (n !== null) {
        if (t = n.tag, t === 13) {
          if (t = rs(n), t !== null) {
            e.blockedOn = t, vs(e.priority, function() {
              ms(n);
            });
            return;
          }
        } else if (t === 3 && n.stateNode.current.memoizedState.isDehydrated) {
          e.blockedOn = n.tag === 3 ? n.stateNode.containerInfo : null;
          return;
        }
      }
    }
    e.blockedOn = null;
  }
  function Ir(e) {
    if (e.blockedOn !== null) return !1;
    for (var t = e.targetContainers; 0 < t.length; ) {
      var n = ti(e.domEventName, e.eventSystemFlags, t[0], e.nativeEvent);
      if (n === null) {
        n = e.nativeEvent;
        var r = new n.constructor(n.type, n);
        Hl = r, n.target.dispatchEvent(r), Hl = null;
      } else return t = or(n), t !== null && Jl(t), e.blockedOn = n, !1;
      t.shift();
    }
    return !0;
  }
  function ws(e, t, n) {
    Ir(e) && n.delete(t);
  }
  function Nc() {
    bl = !1, Rt !== null && Ir(Rt) && (Rt = null), Mt !== null && Ir(Mt) && (Mt = null), zt !== null && Ir(zt) && (zt = null), Wn.forEach(ws), Qn.forEach(ws);
  }
  function Kn(e, t) {
    e.blockedOn === t && (e.blockedOn = null, bl || (bl = !0, g.unstable_scheduleCallback(g.unstable_NormalPriority, Nc)));
  }
  function Yn(e) {
    function t(l) {
      return Kn(l, e);
    }
    if (0 < zr.length) {
      Kn(zr[0], e);
      for (var n = 1; n < zr.length; n++) {
        var r = zr[n];
        r.blockedOn === e && (r.blockedOn = null);
      }
    }
    for (Rt !== null && Kn(Rt, e), Mt !== null && Kn(Mt, e), zt !== null && Kn(zt, e), Wn.forEach(t), Qn.forEach(t), n = 0; n < It.length; n++) r = It[n], r.blockedOn === e && (r.blockedOn = null);
    for (; 0 < It.length && (n = It[0], n.blockedOn === null); ) ys(n), n.blockedOn === null && It.shift();
  }
  var mn = X.ReactCurrentBatchConfig, Dr = !0;
  function Pc(e, t, n, r) {
    var l = de, i = mn.transition;
    mn.transition = null;
    try {
      de = 1, ei(e, t, n, r);
    } finally {
      de = l, mn.transition = i;
    }
  }
  function Tc(e, t, n, r) {
    var l = de, i = mn.transition;
    mn.transition = null;
    try {
      de = 4, ei(e, t, n, r);
    } finally {
      de = l, mn.transition = i;
    }
  }
  function ei(e, t, n, r) {
    if (Dr) {
      var l = ti(e, t, n, r);
      if (l === null) yi(e, t, r, Or, n), gs(e, r);
      else if (Ec(l, e, t, n, r)) r.stopPropagation();
      else if (gs(e, r), t & 4 && -1 < jc.indexOf(e)) {
        for (; l !== null; ) {
          var i = or(l);
          if (i !== null && ps(i), i = ti(e, t, n, r), i === null && yi(e, t, r, Or, n), i === l) break;
          l = i;
        }
        l !== null && r.stopPropagation();
      } else yi(e, t, r, null, n);
    }
  }
  var Or = null;
  function ti(e, t, n, r) {
    if (Or = null, e = Bl(r), e = Zt(e), e !== null) if (t = Xt(e), t === null) e = null;
    else if (n = t.tag, n === 13) {
      if (e = rs(t), e !== null) return e;
      e = null;
    } else if (n === 3) {
      if (t.stateNode.current.memoizedState.isDehydrated) return t.tag === 3 ? t.stateNode.containerInfo : null;
      e = null;
    } else t !== e && (e = null);
    return Or = e, null;
  }
  function xs(e) {
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
        switch (vc()) {
          case Yl:
            return 1;
          case as:
            return 4;
          case Pr:
          case gc:
            return 16;
          case cs:
            return 536870912;
          default:
            return 16;
        }
      default:
        return 16;
    }
  }
  var Dt = null, ni = null, Ar = null;
  function ks() {
    if (Ar) return Ar;
    var e, t = ni, n = t.length, r, l = "value" in Dt ? Dt.value : Dt.textContent, i = l.length;
    for (e = 0; e < n && t[e] === l[e]; e++) ;
    var o = n - e;
    for (r = 1; r <= o && t[n - r] === l[i - r]; r++) ;
    return Ar = l.slice(e, 1 < r ? 1 - r : void 0);
  }
  function Fr(e) {
    var t = e.keyCode;
    return "charCode" in e ? (e = e.charCode, e === 0 && t === 13 && (e = 13)) : e = t, e === 10 && (e = 13), 32 <= e || e === 13 ? e : 0;
  }
  function Ur() {
    return !0;
  }
  function Ss() {
    return !1;
  }
  function tt(e) {
    function t(n, r, l, i, o) {
      this._reactName = n, this._targetInst = l, this.type = r, this.nativeEvent = i, this.target = o, this.currentTarget = null;
      for (var u in e) e.hasOwnProperty(u) && (n = e[u], this[u] = n ? n(i) : i[u]);
      return this.isDefaultPrevented = (i.defaultPrevented != null ? i.defaultPrevented : i.returnValue === !1) ? Ur : Ss, this.isPropagationStopped = Ss, this;
    }
    return O(t.prototype, { preventDefault: function() {
      this.defaultPrevented = !0;
      var n = this.nativeEvent;
      n && (n.preventDefault ? n.preventDefault() : typeof n.returnValue != "unknown" && (n.returnValue = !1), this.isDefaultPrevented = Ur);
    }, stopPropagation: function() {
      var n = this.nativeEvent;
      n && (n.stopPropagation ? n.stopPropagation() : typeof n.cancelBubble != "unknown" && (n.cancelBubble = !0), this.isPropagationStopped = Ur);
    }, persist: function() {
    }, isPersistent: Ur }), t;
  }
  var hn = { eventPhase: 0, bubbles: 0, cancelable: 0, timeStamp: function(e) {
    return e.timeStamp || Date.now();
  }, defaultPrevented: 0, isTrusted: 0 }, ri = tt(hn), qn = O({}, hn, { view: 0, detail: 0 }), Lc = tt(qn), li, ii, Xn, Vr = O({}, qn, { screenX: 0, screenY: 0, clientX: 0, clientY: 0, pageX: 0, pageY: 0, ctrlKey: 0, shiftKey: 0, altKey: 0, metaKey: 0, getModifierState: si, button: 0, buttons: 0, relatedTarget: function(e) {
    return e.relatedTarget === void 0 ? e.fromElement === e.srcElement ? e.toElement : e.fromElement : e.relatedTarget;
  }, movementX: function(e) {
    return "movementX" in e ? e.movementX : (e !== Xn && (Xn && e.type === "mousemove" ? (li = e.screenX - Xn.screenX, ii = e.screenY - Xn.screenY) : ii = li = 0, Xn = e), li);
  }, movementY: function(e) {
    return "movementY" in e ? e.movementY : ii;
  } }), _s = tt(Vr), Rc = O({}, Vr, { dataTransfer: 0 }), Mc = tt(Rc), zc = O({}, qn, { relatedTarget: 0 }), oi = tt(zc), Ic = O({}, hn, { animationName: 0, elapsedTime: 0, pseudoElement: 0 }), Dc = tt(Ic), Oc = O({}, hn, { clipboardData: function(e) {
    return "clipboardData" in e ? e.clipboardData : window.clipboardData;
  } }), Ac = tt(Oc), Fc = O({}, hn, { data: 0 }), Cs = tt(Fc), Uc = {
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
  }, Vc = {
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
  }, $c = { Alt: "altKey", Control: "ctrlKey", Meta: "metaKey", Shift: "shiftKey" };
  function Hc(e) {
    var t = this.nativeEvent;
    return t.getModifierState ? t.getModifierState(e) : (e = $c[e]) ? !!t[e] : !1;
  }
  function si() {
    return Hc;
  }
  var Bc = O({}, qn, { key: function(e) {
    if (e.key) {
      var t = Uc[e.key] || e.key;
      if (t !== "Unidentified") return t;
    }
    return e.type === "keypress" ? (e = Fr(e), e === 13 ? "Enter" : String.fromCharCode(e)) : e.type === "keydown" || e.type === "keyup" ? Vc[e.keyCode] || "Unidentified" : "";
  }, code: 0, location: 0, ctrlKey: 0, shiftKey: 0, altKey: 0, metaKey: 0, repeat: 0, locale: 0, getModifierState: si, charCode: function(e) {
    return e.type === "keypress" ? Fr(e) : 0;
  }, keyCode: function(e) {
    return e.type === "keydown" || e.type === "keyup" ? e.keyCode : 0;
  }, which: function(e) {
    return e.type === "keypress" ? Fr(e) : e.type === "keydown" || e.type === "keyup" ? e.keyCode : 0;
  } }), Wc = tt(Bc), Qc = O({}, Vr, { pointerId: 0, width: 0, height: 0, pressure: 0, tangentialPressure: 0, tiltX: 0, tiltY: 0, twist: 0, pointerType: 0, isPrimary: 0 }), js = tt(Qc), Gc = O({}, qn, { touches: 0, targetTouches: 0, changedTouches: 0, altKey: 0, metaKey: 0, ctrlKey: 0, shiftKey: 0, getModifierState: si }), Kc = tt(Gc), Yc = O({}, hn, { propertyName: 0, elapsedTime: 0, pseudoElement: 0 }), qc = tt(Yc), Xc = O({}, Vr, {
    deltaX: function(e) {
      return "deltaX" in e ? e.deltaX : "wheelDeltaX" in e ? -e.wheelDeltaX : 0;
    },
    deltaY: function(e) {
      return "deltaY" in e ? e.deltaY : "wheelDeltaY" in e ? -e.wheelDeltaY : "wheelDelta" in e ? -e.wheelDelta : 0;
    },
    deltaZ: 0,
    deltaMode: 0
  }), Zc = tt(Xc), Jc = [9, 13, 27, 32], ui = B && "CompositionEvent" in window, Zn = null;
  B && "documentMode" in document && (Zn = document.documentMode);
  var bc = B && "TextEvent" in window && !Zn, Es = B && (!ui || Zn && 8 < Zn && 11 >= Zn), Ns = " ", Ps = !1;
  function Ts(e, t) {
    switch (e) {
      case "keyup":
        return Jc.indexOf(t.keyCode) !== -1;
      case "keydown":
        return t.keyCode !== 229;
      case "keypress":
      case "mousedown":
      case "focusout":
        return !0;
      default:
        return !1;
    }
  }
  function Ls(e) {
    return e = e.detail, typeof e == "object" && "data" in e ? e.data : null;
  }
  var vn = !1;
  function ed(e, t) {
    switch (e) {
      case "compositionend":
        return Ls(t);
      case "keypress":
        return t.which !== 32 ? null : (Ps = !0, Ns);
      case "textInput":
        return e = t.data, e === Ns && Ps ? null : e;
      default:
        return null;
    }
  }
  function td(e, t) {
    if (vn) return e === "compositionend" || !ui && Ts(e, t) ? (e = ks(), Ar = ni = Dt = null, vn = !1, e) : null;
    switch (e) {
      case "paste":
        return null;
      case "keypress":
        if (!(t.ctrlKey || t.altKey || t.metaKey) || t.ctrlKey && t.altKey) {
          if (t.char && 1 < t.char.length) return t.char;
          if (t.which) return String.fromCharCode(t.which);
        }
        return null;
      case "compositionend":
        return Es && t.locale !== "ko" ? null : t.data;
      default:
        return null;
    }
  }
  var nd = { color: !0, date: !0, datetime: !0, "datetime-local": !0, email: !0, month: !0, number: !0, password: !0, range: !0, search: !0, tel: !0, text: !0, time: !0, url: !0, week: !0 };
  function Rs(e) {
    var t = e && e.nodeName && e.nodeName.toLowerCase();
    return t === "input" ? !!nd[e.type] : t === "textarea";
  }
  function Ms(e, t, n, r) {
    Jo(r), t = Qr(t, "onChange"), 0 < t.length && (n = new ri("onChange", "change", null, n, r), e.push({ event: n, listeners: t }));
  }
  var Jn = null, bn = null;
  function rd(e) {
    Xs(e, 0);
  }
  function $r(e) {
    var t = kn(e);
    if (Vo(t)) return e;
  }
  function ld(e, t) {
    if (e === "change") return t;
  }
  var zs = !1;
  if (B) {
    var ai;
    if (B) {
      var ci = "oninput" in document;
      if (!ci) {
        var Is = document.createElement("div");
        Is.setAttribute("oninput", "return;"), ci = typeof Is.oninput == "function";
      }
      ai = ci;
    } else ai = !1;
    zs = ai && (!document.documentMode || 9 < document.documentMode);
  }
  function Ds() {
    Jn && (Jn.detachEvent("onpropertychange", Os), bn = Jn = null);
  }
  function Os(e) {
    if (e.propertyName === "value" && $r(bn)) {
      var t = [];
      Ms(t, bn, e, Bl(e)), ns(rd, t);
    }
  }
  function id(e, t, n) {
    e === "focusin" ? (Ds(), Jn = t, bn = n, Jn.attachEvent("onpropertychange", Os)) : e === "focusout" && Ds();
  }
  function od(e) {
    if (e === "selectionchange" || e === "keyup" || e === "keydown") return $r(bn);
  }
  function sd(e, t) {
    if (e === "click") return $r(t);
  }
  function ud(e, t) {
    if (e === "input" || e === "change") return $r(t);
  }
  function ad(e, t) {
    return e === t && (e !== 0 || 1 / e === 1 / t) || e !== e && t !== t;
  }
  var ft = typeof Object.is == "function" ? Object.is : ad;
  function er(e, t) {
    if (ft(e, t)) return !0;
    if (typeof e != "object" || e === null || typeof t != "object" || t === null) return !1;
    var n = Object.keys(e), r = Object.keys(t);
    if (n.length !== r.length) return !1;
    for (r = 0; r < n.length; r++) {
      var l = n[r];
      if (!_.call(t, l) || !ft(e[l], t[l])) return !1;
    }
    return !0;
  }
  function As(e) {
    for (; e && e.firstChild; ) e = e.firstChild;
    return e;
  }
  function Fs(e, t) {
    var n = As(e);
    e = 0;
    for (var r; n; ) {
      if (n.nodeType === 3) {
        if (r = e + n.textContent.length, e <= t && r >= t) return { node: n, offset: t - e };
        e = r;
      }
      e: {
        for (; n; ) {
          if (n.nextSibling) {
            n = n.nextSibling;
            break e;
          }
          n = n.parentNode;
        }
        n = void 0;
      }
      n = As(n);
    }
  }
  function Us(e, t) {
    return e && t ? e === t ? !0 : e && e.nodeType === 3 ? !1 : t && t.nodeType === 3 ? Us(e, t.parentNode) : "contains" in e ? e.contains(t) : e.compareDocumentPosition ? !!(e.compareDocumentPosition(t) & 16) : !1 : !1;
  }
  function Vs() {
    for (var e = window, t = Cr(); t instanceof e.HTMLIFrameElement; ) {
      try {
        var n = typeof t.contentWindow.location.href == "string";
      } catch {
        n = !1;
      }
      if (n) e = t.contentWindow;
      else break;
      t = Cr(e.document);
    }
    return t;
  }
  function di(e) {
    var t = e && e.nodeName && e.nodeName.toLowerCase();
    return t && (t === "input" && (e.type === "text" || e.type === "search" || e.type === "tel" || e.type === "url" || e.type === "password") || t === "textarea" || e.contentEditable === "true");
  }
  function cd(e) {
    var t = Vs(), n = e.focusedElem, r = e.selectionRange;
    if (t !== n && n && n.ownerDocument && Us(n.ownerDocument.documentElement, n)) {
      if (r !== null && di(n)) {
        if (t = r.start, e = r.end, e === void 0 && (e = t), "selectionStart" in n) n.selectionStart = t, n.selectionEnd = Math.min(e, n.value.length);
        else if (e = (t = n.ownerDocument || document) && t.defaultView || window, e.getSelection) {
          e = e.getSelection();
          var l = n.textContent.length, i = Math.min(r.start, l);
          r = r.end === void 0 ? i : Math.min(r.end, l), !e.extend && i > r && (l = r, r = i, i = l), l = Fs(n, i);
          var o = Fs(
            n,
            r
          );
          l && o && (e.rangeCount !== 1 || e.anchorNode !== l.node || e.anchorOffset !== l.offset || e.focusNode !== o.node || e.focusOffset !== o.offset) && (t = t.createRange(), t.setStart(l.node, l.offset), e.removeAllRanges(), i > r ? (e.addRange(t), e.extend(o.node, o.offset)) : (t.setEnd(o.node, o.offset), e.addRange(t)));
        }
      }
      for (t = [], e = n; e = e.parentNode; ) e.nodeType === 1 && t.push({ element: e, left: e.scrollLeft, top: e.scrollTop });
      for (typeof n.focus == "function" && n.focus(), n = 0; n < t.length; n++) e = t[n], e.element.scrollLeft = e.left, e.element.scrollTop = e.top;
    }
  }
  var dd = B && "documentMode" in document && 11 >= document.documentMode, gn = null, fi = null, tr = null, pi = !1;
  function $s(e, t, n) {
    var r = n.window === n ? n.document : n.nodeType === 9 ? n : n.ownerDocument;
    pi || gn == null || gn !== Cr(r) || (r = gn, "selectionStart" in r && di(r) ? r = { start: r.selectionStart, end: r.selectionEnd } : (r = (r.ownerDocument && r.ownerDocument.defaultView || window).getSelection(), r = { anchorNode: r.anchorNode, anchorOffset: r.anchorOffset, focusNode: r.focusNode, focusOffset: r.focusOffset }), tr && er(tr, r) || (tr = r, r = Qr(fi, "onSelect"), 0 < r.length && (t = new ri("onSelect", "select", null, t, n), e.push({ event: t, listeners: r }), t.target = gn)));
  }
  function Hr(e, t) {
    var n = {};
    return n[e.toLowerCase()] = t.toLowerCase(), n["Webkit" + e] = "webkit" + t, n["Moz" + e] = "moz" + t, n;
  }
  var yn = { animationend: Hr("Animation", "AnimationEnd"), animationiteration: Hr("Animation", "AnimationIteration"), animationstart: Hr("Animation", "AnimationStart"), transitionend: Hr("Transition", "TransitionEnd") }, mi = {}, Hs = {};
  B && (Hs = document.createElement("div").style, "AnimationEvent" in window || (delete yn.animationend.animation, delete yn.animationiteration.animation, delete yn.animationstart.animation), "TransitionEvent" in window || delete yn.transitionend.transition);
  function Br(e) {
    if (mi[e]) return mi[e];
    if (!yn[e]) return e;
    var t = yn[e], n;
    for (n in t) if (t.hasOwnProperty(n) && n in Hs) return mi[e] = t[n];
    return e;
  }
  var Bs = Br("animationend"), Ws = Br("animationiteration"), Qs = Br("animationstart"), Gs = Br("transitionend"), Ks = /* @__PURE__ */ new Map(), Ys = "abort auxClick cancel canPlay canPlayThrough click close contextMenu copy cut drag dragEnd dragEnter dragExit dragLeave dragOver dragStart drop durationChange emptied encrypted ended error gotPointerCapture input invalid keyDown keyPress keyUp load loadedData loadedMetadata loadStart lostPointerCapture mouseDown mouseMove mouseOut mouseOver mouseUp paste pause play playing pointerCancel pointerDown pointerMove pointerOut pointerOver pointerUp progress rateChange reset resize seeked seeking stalled submit suspend timeUpdate touchCancel touchEnd touchStart volumeChange scroll toggle touchMove waiting wheel".split(" ");
  function Ot(e, t) {
    Ks.set(e, t), D(t, [e]);
  }
  for (var hi = 0; hi < Ys.length; hi++) {
    var vi = Ys[hi], fd = vi.toLowerCase(), pd = vi[0].toUpperCase() + vi.slice(1);
    Ot(fd, "on" + pd);
  }
  Ot(Bs, "onAnimationEnd"), Ot(Ws, "onAnimationIteration"), Ot(Qs, "onAnimationStart"), Ot("dblclick", "onDoubleClick"), Ot("focusin", "onFocus"), Ot("focusout", "onBlur"), Ot(Gs, "onTransitionEnd"), F("onMouseEnter", ["mouseout", "mouseover"]), F("onMouseLeave", ["mouseout", "mouseover"]), F("onPointerEnter", ["pointerout", "pointerover"]), F("onPointerLeave", ["pointerout", "pointerover"]), D("onChange", "change click focusin focusout input keydown keyup selectionchange".split(" ")), D("onSelect", "focusout contextmenu dragend focusin keydown keyup mousedown mouseup selectionchange".split(" ")), D("onBeforeInput", ["compositionend", "keypress", "textInput", "paste"]), D("onCompositionEnd", "compositionend focusout keydown keypress keyup mousedown".split(" ")), D("onCompositionStart", "compositionstart focusout keydown keypress keyup mousedown".split(" ")), D("onCompositionUpdate", "compositionupdate focusout keydown keypress keyup mousedown".split(" "));
  var nr = "abort canplay canplaythrough durationchange emptied encrypted ended error loadeddata loadedmetadata loadstart pause play playing progress ratechange resize seeked seeking stalled suspend timeupdate volumechange waiting".split(" "), md = new Set("cancel close invalid load scroll toggle".split(" ").concat(nr));
  function qs(e, t, n) {
    var r = e.type || "unknown-event";
    e.currentTarget = n, fc(r, t, void 0, e), e.currentTarget = null;
  }
  function Xs(e, t) {
    t = (t & 4) !== 0;
    for (var n = 0; n < e.length; n++) {
      var r = e[n], l = r.event;
      r = r.listeners;
      e: {
        var i = void 0;
        if (t) for (var o = r.length - 1; 0 <= o; o--) {
          var u = r[o], d = u.instance, y = u.currentTarget;
          if (u = u.listener, d !== i && l.isPropagationStopped()) break e;
          qs(l, u, y), i = d;
        }
        else for (o = 0; o < r.length; o++) {
          if (u = r[o], d = u.instance, y = u.currentTarget, u = u.listener, d !== i && l.isPropagationStopped()) break e;
          qs(l, u, y), i = d;
        }
      }
    }
    if (Nr) throw e = Kl, Nr = !1, Kl = null, e;
  }
  function he(e, t) {
    var n = t[Ci];
    n === void 0 && (n = t[Ci] = /* @__PURE__ */ new Set());
    var r = e + "__bubble";
    n.has(r) || (Zs(t, e, 2, !1), n.add(r));
  }
  function gi(e, t, n) {
    var r = 0;
    t && (r |= 4), Zs(n, e, r, t);
  }
  var Wr = "_reactListening" + Math.random().toString(36).slice(2);
  function rr(e) {
    if (!e[Wr]) {
      e[Wr] = !0, P.forEach(function(n) {
        n !== "selectionchange" && (md.has(n) || gi(n, !1, e), gi(n, !0, e));
      });
      var t = e.nodeType === 9 ? e : e.ownerDocument;
      t === null || t[Wr] || (t[Wr] = !0, gi("selectionchange", !1, t));
    }
  }
  function Zs(e, t, n, r) {
    switch (xs(t)) {
      case 1:
        var l = Pc;
        break;
      case 4:
        l = Tc;
        break;
      default:
        l = ei;
    }
    n = l.bind(null, t, n, e), l = void 0, !Gl || t !== "touchstart" && t !== "touchmove" && t !== "wheel" || (l = !0), r ? l !== void 0 ? e.addEventListener(t, n, { capture: !0, passive: l }) : e.addEventListener(t, n, !0) : l !== void 0 ? e.addEventListener(t, n, { passive: l }) : e.addEventListener(t, n, !1);
  }
  function yi(e, t, n, r, l) {
    var i = r;
    if ((t & 1) === 0 && (t & 2) === 0 && r !== null) e: for (; ; ) {
      if (r === null) return;
      var o = r.tag;
      if (o === 3 || o === 4) {
        var u = r.stateNode.containerInfo;
        if (u === l || u.nodeType === 8 && u.parentNode === l) break;
        if (o === 4) for (o = r.return; o !== null; ) {
          var d = o.tag;
          if ((d === 3 || d === 4) && (d = o.stateNode.containerInfo, d === l || d.nodeType === 8 && d.parentNode === l)) return;
          o = o.return;
        }
        for (; u !== null; ) {
          if (o = Zt(u), o === null) return;
          if (d = o.tag, d === 5 || d === 6) {
            r = i = o;
            continue e;
          }
          u = u.parentNode;
        }
      }
      r = r.return;
    }
    ns(function() {
      var y = i, j = Bl(n), N = [];
      e: {
        var C = Ks.get(e);
        if (C !== void 0) {
          var I = ri, U = e;
          switch (e) {
            case "keypress":
              if (Fr(n) === 0) break e;
            case "keydown":
            case "keyup":
              I = Wc;
              break;
            case "focusin":
              U = "focus", I = oi;
              break;
            case "focusout":
              U = "blur", I = oi;
              break;
            case "beforeblur":
            case "afterblur":
              I = oi;
              break;
            case "click":
              if (n.button === 2) break e;
            case "auxclick":
            case "dblclick":
            case "mousedown":
            case "mousemove":
            case "mouseup":
            case "mouseout":
            case "mouseover":
            case "contextmenu":
              I = _s;
              break;
            case "drag":
            case "dragend":
            case "dragenter":
            case "dragexit":
            case "dragleave":
            case "dragover":
            case "dragstart":
            case "drop":
              I = Mc;
              break;
            case "touchcancel":
            case "touchend":
            case "touchmove":
            case "touchstart":
              I = Kc;
              break;
            case Bs:
            case Ws:
            case Qs:
              I = Dc;
              break;
            case Gs:
              I = qc;
              break;
            case "scroll":
              I = Lc;
              break;
            case "wheel":
              I = Zc;
              break;
            case "copy":
            case "cut":
            case "paste":
              I = Ac;
              break;
            case "gotpointercapture":
            case "lostpointercapture":
            case "pointercancel":
            case "pointerdown":
            case "pointermove":
            case "pointerout":
            case "pointerover":
            case "pointerup":
              I = js;
          }
          var V = (t & 4) !== 0, Ne = !V && e === "scroll", h = V ? C !== null ? C + "Capture" : null : C;
          V = [];
          for (var p = y, v; p !== null; ) {
            v = p;
            var T = v.stateNode;
            if (v.tag === 5 && T !== null && (v = T, h !== null && (T = Un(p, h), T != null && V.push(lr(p, T, v)))), Ne) break;
            p = p.return;
          }
          0 < V.length && (C = new I(C, U, null, n, j), N.push({ event: C, listeners: V }));
        }
      }
      if ((t & 7) === 0) {
        e: {
          if (C = e === "mouseover" || e === "pointerover", I = e === "mouseout" || e === "pointerout", C && n !== Hl && (U = n.relatedTarget || n.fromElement) && (Zt(U) || U[_t])) break e;
          if ((I || C) && (C = j.window === j ? j : (C = j.ownerDocument) ? C.defaultView || C.parentWindow : window, I ? (U = n.relatedTarget || n.toElement, I = y, U = U ? Zt(U) : null, U !== null && (Ne = Xt(U), U !== Ne || U.tag !== 5 && U.tag !== 6) && (U = null)) : (I = null, U = y), I !== U)) {
            if (V = _s, T = "onMouseLeave", h = "onMouseEnter", p = "mouse", (e === "pointerout" || e === "pointerover") && (V = js, T = "onPointerLeave", h = "onPointerEnter", p = "pointer"), Ne = I == null ? C : kn(I), v = U == null ? C : kn(U), C = new V(T, p + "leave", I, n, j), C.target = Ne, C.relatedTarget = v, T = null, Zt(j) === y && (V = new V(h, p + "enter", U, n, j), V.target = v, V.relatedTarget = Ne, T = V), Ne = T, I && U) t: {
              for (V = I, h = U, p = 0, v = V; v; v = wn(v)) p++;
              for (v = 0, T = h; T; T = wn(T)) v++;
              for (; 0 < p - v; ) V = wn(V), p--;
              for (; 0 < v - p; ) h = wn(h), v--;
              for (; p--; ) {
                if (V === h || h !== null && V === h.alternate) break t;
                V = wn(V), h = wn(h);
              }
              V = null;
            }
            else V = null;
            I !== null && Js(N, C, I, V, !1), U !== null && Ne !== null && Js(N, Ne, U, V, !0);
          }
        }
        e: {
          if (C = y ? kn(y) : window, I = C.nodeName && C.nodeName.toLowerCase(), I === "select" || I === "input" && C.type === "file") var H = ld;
          else if (Rs(C)) if (zs) H = ud;
          else {
            H = od;
            var G = id;
          }
          else (I = C.nodeName) && I.toLowerCase() === "input" && (C.type === "checkbox" || C.type === "radio") && (H = sd);
          if (H && (H = H(e, y))) {
            Ms(N, H, n, j);
            break e;
          }
          G && G(e, C, y), e === "focusout" && (G = C._wrapperState) && G.controlled && C.type === "number" && Al(C, "number", C.value);
        }
        switch (G = y ? kn(y) : window, e) {
          case "focusin":
            (Rs(G) || G.contentEditable === "true") && (gn = G, fi = y, tr = null);
            break;
          case "focusout":
            tr = fi = gn = null;
            break;
          case "mousedown":
            pi = !0;
            break;
          case "contextmenu":
          case "mouseup":
          case "dragend":
            pi = !1, $s(N, n, j);
            break;
          case "selectionchange":
            if (dd) break;
          case "keydown":
          case "keyup":
            $s(N, n, j);
        }
        var K;
        if (ui) e: {
          switch (e) {
            case "compositionstart":
              var q = "onCompositionStart";
              break e;
            case "compositionend":
              q = "onCompositionEnd";
              break e;
            case "compositionupdate":
              q = "onCompositionUpdate";
              break e;
          }
          q = void 0;
        }
        else vn ? Ts(e, n) && (q = "onCompositionEnd") : e === "keydown" && n.keyCode === 229 && (q = "onCompositionStart");
        q && (Es && n.locale !== "ko" && (vn || q !== "onCompositionStart" ? q === "onCompositionEnd" && vn && (K = ks()) : (Dt = j, ni = "value" in Dt ? Dt.value : Dt.textContent, vn = !0)), G = Qr(y, q), 0 < G.length && (q = new Cs(q, e, null, n, j), N.push({ event: q, listeners: G }), K ? q.data = K : (K = Ls(n), K !== null && (q.data = K)))), (K = bc ? ed(e, n) : td(e, n)) && (y = Qr(y, "onBeforeInput"), 0 < y.length && (j = new Cs("onBeforeInput", "beforeinput", null, n, j), N.push({ event: j, listeners: y }), j.data = K));
      }
      Xs(N, t);
    });
  }
  function lr(e, t, n) {
    return { instance: e, listener: t, currentTarget: n };
  }
  function Qr(e, t) {
    for (var n = t + "Capture", r = []; e !== null; ) {
      var l = e, i = l.stateNode;
      l.tag === 5 && i !== null && (l = i, i = Un(e, n), i != null && r.unshift(lr(e, i, l)), i = Un(e, t), i != null && r.push(lr(e, i, l))), e = e.return;
    }
    return r;
  }
  function wn(e) {
    if (e === null) return null;
    do
      e = e.return;
    while (e && e.tag !== 5);
    return e || null;
  }
  function Js(e, t, n, r, l) {
    for (var i = t._reactName, o = []; n !== null && n !== r; ) {
      var u = n, d = u.alternate, y = u.stateNode;
      if (d !== null && d === r) break;
      u.tag === 5 && y !== null && (u = y, l ? (d = Un(n, i), d != null && o.unshift(lr(n, d, u))) : l || (d = Un(n, i), d != null && o.push(lr(n, d, u)))), n = n.return;
    }
    o.length !== 0 && e.push({ event: t, listeners: o });
  }
  var hd = /\r\n?/g, vd = /\u0000|\uFFFD/g;
  function bs(e) {
    return (typeof e == "string" ? e : "" + e).replace(hd, `
`).replace(vd, "");
  }
  function Gr(e, t, n) {
    if (t = bs(t), bs(e) !== t && n) throw Error(a(425));
  }
  function Kr() {
  }
  var wi = null, xi = null;
  function ki(e, t) {
    return e === "textarea" || e === "noscript" || typeof t.children == "string" || typeof t.children == "number" || typeof t.dangerouslySetInnerHTML == "object" && t.dangerouslySetInnerHTML !== null && t.dangerouslySetInnerHTML.__html != null;
  }
  var Si = typeof setTimeout == "function" ? setTimeout : void 0, gd = typeof clearTimeout == "function" ? clearTimeout : void 0, eu = typeof Promise == "function" ? Promise : void 0, yd = typeof queueMicrotask == "function" ? queueMicrotask : typeof eu < "u" ? function(e) {
    return eu.resolve(null).then(e).catch(wd);
  } : Si;
  function wd(e) {
    setTimeout(function() {
      throw e;
    });
  }
  function _i(e, t) {
    var n = t, r = 0;
    do {
      var l = n.nextSibling;
      if (e.removeChild(n), l && l.nodeType === 8) if (n = l.data, n === "/$") {
        if (r === 0) {
          e.removeChild(l), Yn(t);
          return;
        }
        r--;
      } else n !== "$" && n !== "$?" && n !== "$!" || r++;
      n = l;
    } while (n);
    Yn(t);
  }
  function At(e) {
    for (; e != null; e = e.nextSibling) {
      var t = e.nodeType;
      if (t === 1 || t === 3) break;
      if (t === 8) {
        if (t = e.data, t === "$" || t === "$!" || t === "$?") break;
        if (t === "/$") return null;
      }
    }
    return e;
  }
  function tu(e) {
    e = e.previousSibling;
    for (var t = 0; e; ) {
      if (e.nodeType === 8) {
        var n = e.data;
        if (n === "$" || n === "$!" || n === "$?") {
          if (t === 0) return e;
          t--;
        } else n === "/$" && t++;
      }
      e = e.previousSibling;
    }
    return null;
  }
  var xn = Math.random().toString(36).slice(2), wt = "__reactFiber$" + xn, ir = "__reactProps$" + xn, _t = "__reactContainer$" + xn, Ci = "__reactEvents$" + xn, xd = "__reactListeners$" + xn, kd = "__reactHandles$" + xn;
  function Zt(e) {
    var t = e[wt];
    if (t) return t;
    for (var n = e.parentNode; n; ) {
      if (t = n[_t] || n[wt]) {
        if (n = t.alternate, t.child !== null || n !== null && n.child !== null) for (e = tu(e); e !== null; ) {
          if (n = e[wt]) return n;
          e = tu(e);
        }
        return t;
      }
      e = n, n = e.parentNode;
    }
    return null;
  }
  function or(e) {
    return e = e[wt] || e[_t], !e || e.tag !== 5 && e.tag !== 6 && e.tag !== 13 && e.tag !== 3 ? null : e;
  }
  function kn(e) {
    if (e.tag === 5 || e.tag === 6) return e.stateNode;
    throw Error(a(33));
  }
  function Yr(e) {
    return e[ir] || null;
  }
  var ji = [], Sn = -1;
  function Ft(e) {
    return { current: e };
  }
  function ve(e) {
    0 > Sn || (e.current = ji[Sn], ji[Sn] = null, Sn--);
  }
  function me(e, t) {
    Sn++, ji[Sn] = e.current, e.current = t;
  }
  var Ut = {}, Ve = Ft(Ut), Ye = Ft(!1), Jt = Ut;
  function _n(e, t) {
    var n = e.type.contextTypes;
    if (!n) return Ut;
    var r = e.stateNode;
    if (r && r.__reactInternalMemoizedUnmaskedChildContext === t) return r.__reactInternalMemoizedMaskedChildContext;
    var l = {}, i;
    for (i in n) l[i] = t[i];
    return r && (e = e.stateNode, e.__reactInternalMemoizedUnmaskedChildContext = t, e.__reactInternalMemoizedMaskedChildContext = l), l;
  }
  function qe(e) {
    return e = e.childContextTypes, e != null;
  }
  function qr() {
    ve(Ye), ve(Ve);
  }
  function nu(e, t, n) {
    if (Ve.current !== Ut) throw Error(a(168));
    me(Ve, t), me(Ye, n);
  }
  function ru(e, t, n) {
    var r = e.stateNode;
    if (t = t.childContextTypes, typeof r.getChildContext != "function") return n;
    r = r.getChildContext();
    for (var l in r) if (!(l in t)) throw Error(a(108, pe(e) || "Unknown", l));
    return O({}, n, r);
  }
  function Xr(e) {
    return e = (e = e.stateNode) && e.__reactInternalMemoizedMergedChildContext || Ut, Jt = Ve.current, me(Ve, e), me(Ye, Ye.current), !0;
  }
  function lu(e, t, n) {
    var r = e.stateNode;
    if (!r) throw Error(a(169));
    n ? (e = ru(e, t, Jt), r.__reactInternalMemoizedMergedChildContext = e, ve(Ye), ve(Ve), me(Ve, e)) : ve(Ye), me(Ye, n);
  }
  var Ct = null, Zr = !1, Ei = !1;
  function iu(e) {
    Ct === null ? Ct = [e] : Ct.push(e);
  }
  function Sd(e) {
    Zr = !0, iu(e);
  }
  function Vt() {
    if (!Ei && Ct !== null) {
      Ei = !0;
      var e = 0, t = de;
      try {
        var n = Ct;
        for (de = 1; e < n.length; e++) {
          var r = n[e];
          do
            r = r(!0);
          while (r !== null);
        }
        Ct = null, Zr = !1;
      } catch (l) {
        throw Ct !== null && (Ct = Ct.slice(e + 1)), ss(Yl, Vt), l;
      } finally {
        de = t, Ei = !1;
      }
    }
    return null;
  }
  var Cn = [], jn = 0, Jr = null, br = 0, it = [], ot = 0, bt = null, jt = 1, Et = "";
  function en(e, t) {
    Cn[jn++] = br, Cn[jn++] = Jr, Jr = e, br = t;
  }
  function ou(e, t, n) {
    it[ot++] = jt, it[ot++] = Et, it[ot++] = bt, bt = e;
    var r = jt;
    e = Et;
    var l = 32 - dt(r) - 1;
    r &= ~(1 << l), n += 1;
    var i = 32 - dt(t) + l;
    if (30 < i) {
      var o = l - l % 5;
      i = (r & (1 << o) - 1).toString(32), r >>= o, l -= o, jt = 1 << 32 - dt(t) + l | n << l | r, Et = i + e;
    } else jt = 1 << i | n << l | r, Et = e;
  }
  function Ni(e) {
    e.return !== null && (en(e, 1), ou(e, 1, 0));
  }
  function Pi(e) {
    for (; e === Jr; ) Jr = Cn[--jn], Cn[jn] = null, br = Cn[--jn], Cn[jn] = null;
    for (; e === bt; ) bt = it[--ot], it[ot] = null, Et = it[--ot], it[ot] = null, jt = it[--ot], it[ot] = null;
  }
  var nt = null, rt = null, we = !1, pt = null;
  function su(e, t) {
    var n = ct(5, null, null, 0);
    n.elementType = "DELETED", n.stateNode = t, n.return = e, t = e.deletions, t === null ? (e.deletions = [n], e.flags |= 16) : t.push(n);
  }
  function uu(e, t) {
    switch (e.tag) {
      case 5:
        var n = e.type;
        return t = t.nodeType !== 1 || n.toLowerCase() !== t.nodeName.toLowerCase() ? null : t, t !== null ? (e.stateNode = t, nt = e, rt = At(t.firstChild), !0) : !1;
      case 6:
        return t = e.pendingProps === "" || t.nodeType !== 3 ? null : t, t !== null ? (e.stateNode = t, nt = e, rt = null, !0) : !1;
      case 13:
        return t = t.nodeType !== 8 ? null : t, t !== null ? (n = bt !== null ? { id: jt, overflow: Et } : null, e.memoizedState = { dehydrated: t, treeContext: n, retryLane: 1073741824 }, n = ct(18, null, null, 0), n.stateNode = t, n.return = e, e.child = n, nt = e, rt = null, !0) : !1;
      default:
        return !1;
    }
  }
  function Ti(e) {
    return (e.mode & 1) !== 0 && (e.flags & 128) === 0;
  }
  function Li(e) {
    if (we) {
      var t = rt;
      if (t) {
        var n = t;
        if (!uu(e, t)) {
          if (Ti(e)) throw Error(a(418));
          t = At(n.nextSibling);
          var r = nt;
          t && uu(e, t) ? su(r, n) : (e.flags = e.flags & -4097 | 2, we = !1, nt = e);
        }
      } else {
        if (Ti(e)) throw Error(a(418));
        e.flags = e.flags & -4097 | 2, we = !1, nt = e;
      }
    }
  }
  function au(e) {
    for (e = e.return; e !== null && e.tag !== 5 && e.tag !== 3 && e.tag !== 13; ) e = e.return;
    nt = e;
  }
  function el(e) {
    if (e !== nt) return !1;
    if (!we) return au(e), we = !0, !1;
    var t;
    if ((t = e.tag !== 3) && !(t = e.tag !== 5) && (t = e.type, t = t !== "head" && t !== "body" && !ki(e.type, e.memoizedProps)), t && (t = rt)) {
      if (Ti(e)) throw cu(), Error(a(418));
      for (; t; ) su(e, t), t = At(t.nextSibling);
    }
    if (au(e), e.tag === 13) {
      if (e = e.memoizedState, e = e !== null ? e.dehydrated : null, !e) throw Error(a(317));
      e: {
        for (e = e.nextSibling, t = 0; e; ) {
          if (e.nodeType === 8) {
            var n = e.data;
            if (n === "/$") {
              if (t === 0) {
                rt = At(e.nextSibling);
                break e;
              }
              t--;
            } else n !== "$" && n !== "$!" && n !== "$?" || t++;
          }
          e = e.nextSibling;
        }
        rt = null;
      }
    } else rt = nt ? At(e.stateNode.nextSibling) : null;
    return !0;
  }
  function cu() {
    for (var e = rt; e; ) e = At(e.nextSibling);
  }
  function En() {
    rt = nt = null, we = !1;
  }
  function Ri(e) {
    pt === null ? pt = [e] : pt.push(e);
  }
  var _d = X.ReactCurrentBatchConfig;
  function sr(e, t, n) {
    if (e = n.ref, e !== null && typeof e != "function" && typeof e != "object") {
      if (n._owner) {
        if (n = n._owner, n) {
          if (n.tag !== 1) throw Error(a(309));
          var r = n.stateNode;
        }
        if (!r) throw Error(a(147, e));
        var l = r, i = "" + e;
        return t !== null && t.ref !== null && typeof t.ref == "function" && t.ref._stringRef === i ? t.ref : (t = function(o) {
          var u = l.refs;
          o === null ? delete u[i] : u[i] = o;
        }, t._stringRef = i, t);
      }
      if (typeof e != "string") throw Error(a(284));
      if (!n._owner) throw Error(a(290, e));
    }
    return e;
  }
  function tl(e, t) {
    throw e = Object.prototype.toString.call(t), Error(a(31, e === "[object Object]" ? "object with keys {" + Object.keys(t).join(", ") + "}" : e));
  }
  function du(e) {
    var t = e._init;
    return t(e._payload);
  }
  function fu(e) {
    function t(h, p) {
      if (e) {
        var v = h.deletions;
        v === null ? (h.deletions = [p], h.flags |= 16) : v.push(p);
      }
    }
    function n(h, p) {
      if (!e) return null;
      for (; p !== null; ) t(h, p), p = p.sibling;
      return null;
    }
    function r(h, p) {
      for (h = /* @__PURE__ */ new Map(); p !== null; ) p.key !== null ? h.set(p.key, p) : h.set(p.index, p), p = p.sibling;
      return h;
    }
    function l(h, p) {
      return h = Yt(h, p), h.index = 0, h.sibling = null, h;
    }
    function i(h, p, v) {
      return h.index = v, e ? (v = h.alternate, v !== null ? (v = v.index, v < p ? (h.flags |= 2, p) : v) : (h.flags |= 2, p)) : (h.flags |= 1048576, p);
    }
    function o(h) {
      return e && h.alternate === null && (h.flags |= 2), h;
    }
    function u(h, p, v, T) {
      return p === null || p.tag !== 6 ? (p = _o(v, h.mode, T), p.return = h, p) : (p = l(p, v), p.return = h, p);
    }
    function d(h, p, v, T) {
      var H = v.type;
      return H === Z ? j(h, p, v.props.children, T, v.key) : p !== null && (p.elementType === H || typeof H == "object" && H !== null && H.$$typeof === Le && du(H) === p.type) ? (T = l(p, v.props), T.ref = sr(h, p, v), T.return = h, T) : (T = jl(v.type, v.key, v.props, null, h.mode, T), T.ref = sr(h, p, v), T.return = h, T);
    }
    function y(h, p, v, T) {
      return p === null || p.tag !== 4 || p.stateNode.containerInfo !== v.containerInfo || p.stateNode.implementation !== v.implementation ? (p = Co(v, h.mode, T), p.return = h, p) : (p = l(p, v.children || []), p.return = h, p);
    }
    function j(h, p, v, T, H) {
      return p === null || p.tag !== 7 ? (p = an(v, h.mode, T, H), p.return = h, p) : (p = l(p, v), p.return = h, p);
    }
    function N(h, p, v) {
      if (typeof p == "string" && p !== "" || typeof p == "number") return p = _o("" + p, h.mode, v), p.return = h, p;
      if (typeof p == "object" && p !== null) {
        switch (p.$$typeof) {
          case J:
            return v = jl(p.type, p.key, p.props, null, h.mode, v), v.ref = sr(h, null, p), v.return = h, v;
          case b:
            return p = Co(p, h.mode, v), p.return = h, p;
          case Le:
            var T = p._init;
            return N(h, T(p._payload), v);
        }
        if (On(p) || Q(p)) return p = an(p, h.mode, v, null), p.return = h, p;
        tl(h, p);
      }
      return null;
    }
    function C(h, p, v, T) {
      var H = p !== null ? p.key : null;
      if (typeof v == "string" && v !== "" || typeof v == "number") return H !== null ? null : u(h, p, "" + v, T);
      if (typeof v == "object" && v !== null) {
        switch (v.$$typeof) {
          case J:
            return v.key === H ? d(h, p, v, T) : null;
          case b:
            return v.key === H ? y(h, p, v, T) : null;
          case Le:
            return H = v._init, C(
              h,
              p,
              H(v._payload),
              T
            );
        }
        if (On(v) || Q(v)) return H !== null ? null : j(h, p, v, T, null);
        tl(h, v);
      }
      return null;
    }
    function I(h, p, v, T, H) {
      if (typeof T == "string" && T !== "" || typeof T == "number") return h = h.get(v) || null, u(p, h, "" + T, H);
      if (typeof T == "object" && T !== null) {
        switch (T.$$typeof) {
          case J:
            return h = h.get(T.key === null ? v : T.key) || null, d(p, h, T, H);
          case b:
            return h = h.get(T.key === null ? v : T.key) || null, y(p, h, T, H);
          case Le:
            var G = T._init;
            return I(h, p, v, G(T._payload), H);
        }
        if (On(T) || Q(T)) return h = h.get(v) || null, j(p, h, T, H, null);
        tl(p, T);
      }
      return null;
    }
    function U(h, p, v, T) {
      for (var H = null, G = null, K = p, q = p = 0, De = null; K !== null && q < v.length; q++) {
        K.index > q ? (De = K, K = null) : De = K.sibling;
        var oe = C(h, K, v[q], T);
        if (oe === null) {
          K === null && (K = De);
          break;
        }
        e && K && oe.alternate === null && t(h, K), p = i(oe, p, q), G === null ? H = oe : G.sibling = oe, G = oe, K = De;
      }
      if (q === v.length) return n(h, K), we && en(h, q), H;
      if (K === null) {
        for (; q < v.length; q++) K = N(h, v[q], T), K !== null && (p = i(K, p, q), G === null ? H = K : G.sibling = K, G = K);
        return we && en(h, q), H;
      }
      for (K = r(h, K); q < v.length; q++) De = I(K, h, q, v[q], T), De !== null && (e && De.alternate !== null && K.delete(De.key === null ? q : De.key), p = i(De, p, q), G === null ? H = De : G.sibling = De, G = De);
      return e && K.forEach(function(qt) {
        return t(h, qt);
      }), we && en(h, q), H;
    }
    function V(h, p, v, T) {
      var H = Q(v);
      if (typeof H != "function") throw Error(a(150));
      if (v = H.call(v), v == null) throw Error(a(151));
      for (var G = H = null, K = p, q = p = 0, De = null, oe = v.next(); K !== null && !oe.done; q++, oe = v.next()) {
        K.index > q ? (De = K, K = null) : De = K.sibling;
        var qt = C(h, K, oe.value, T);
        if (qt === null) {
          K === null && (K = De);
          break;
        }
        e && K && qt.alternate === null && t(h, K), p = i(qt, p, q), G === null ? H = qt : G.sibling = qt, G = qt, K = De;
      }
      if (oe.done) return n(
        h,
        K
      ), we && en(h, q), H;
      if (K === null) {
        for (; !oe.done; q++, oe = v.next()) oe = N(h, oe.value, T), oe !== null && (p = i(oe, p, q), G === null ? H = oe : G.sibling = oe, G = oe);
        return we && en(h, q), H;
      }
      for (K = r(h, K); !oe.done; q++, oe = v.next()) oe = I(K, h, q, oe.value, T), oe !== null && (e && oe.alternate !== null && K.delete(oe.key === null ? q : oe.key), p = i(oe, p, q), G === null ? H = oe : G.sibling = oe, G = oe);
      return e && K.forEach(function(nf) {
        return t(h, nf);
      }), we && en(h, q), H;
    }
    function Ne(h, p, v, T) {
      if (typeof v == "object" && v !== null && v.type === Z && v.key === null && (v = v.props.children), typeof v == "object" && v !== null) {
        switch (v.$$typeof) {
          case J:
            e: {
              for (var H = v.key, G = p; G !== null; ) {
                if (G.key === H) {
                  if (H = v.type, H === Z) {
                    if (G.tag === 7) {
                      n(h, G.sibling), p = l(G, v.props.children), p.return = h, h = p;
                      break e;
                    }
                  } else if (G.elementType === H || typeof H == "object" && H !== null && H.$$typeof === Le && du(H) === G.type) {
                    n(h, G.sibling), p = l(G, v.props), p.ref = sr(h, G, v), p.return = h, h = p;
                    break e;
                  }
                  n(h, G);
                  break;
                } else t(h, G);
                G = G.sibling;
              }
              v.type === Z ? (p = an(v.props.children, h.mode, T, v.key), p.return = h, h = p) : (T = jl(v.type, v.key, v.props, null, h.mode, T), T.ref = sr(h, p, v), T.return = h, h = T);
            }
            return o(h);
          case b:
            e: {
              for (G = v.key; p !== null; ) {
                if (p.key === G) if (p.tag === 4 && p.stateNode.containerInfo === v.containerInfo && p.stateNode.implementation === v.implementation) {
                  n(h, p.sibling), p = l(p, v.children || []), p.return = h, h = p;
                  break e;
                } else {
                  n(h, p);
                  break;
                }
                else t(h, p);
                p = p.sibling;
              }
              p = Co(v, h.mode, T), p.return = h, h = p;
            }
            return o(h);
          case Le:
            return G = v._init, Ne(h, p, G(v._payload), T);
        }
        if (On(v)) return U(h, p, v, T);
        if (Q(v)) return V(h, p, v, T);
        tl(h, v);
      }
      return typeof v == "string" && v !== "" || typeof v == "number" ? (v = "" + v, p !== null && p.tag === 6 ? (n(h, p.sibling), p = l(p, v), p.return = h, h = p) : (n(h, p), p = _o(v, h.mode, T), p.return = h, h = p), o(h)) : n(h, p);
    }
    return Ne;
  }
  var Nn = fu(!0), pu = fu(!1), nl = Ft(null), rl = null, Pn = null, Mi = null;
  function zi() {
    Mi = Pn = rl = null;
  }
  function Ii(e) {
    var t = nl.current;
    ve(nl), e._currentValue = t;
  }
  function Di(e, t, n) {
    for (; e !== null; ) {
      var r = e.alternate;
      if ((e.childLanes & t) !== t ? (e.childLanes |= t, r !== null && (r.childLanes |= t)) : r !== null && (r.childLanes & t) !== t && (r.childLanes |= t), e === n) break;
      e = e.return;
    }
  }
  function Tn(e, t) {
    rl = e, Mi = Pn = null, e = e.dependencies, e !== null && e.firstContext !== null && ((e.lanes & t) !== 0 && (Xe = !0), e.firstContext = null);
  }
  function st(e) {
    var t = e._currentValue;
    if (Mi !== e) if (e = { context: e, memoizedValue: t, next: null }, Pn === null) {
      if (rl === null) throw Error(a(308));
      Pn = e, rl.dependencies = { lanes: 0, firstContext: e };
    } else Pn = Pn.next = e;
    return t;
  }
  var tn = null;
  function Oi(e) {
    tn === null ? tn = [e] : tn.push(e);
  }
  function mu(e, t, n, r) {
    var l = t.interleaved;
    return l === null ? (n.next = n, Oi(t)) : (n.next = l.next, l.next = n), t.interleaved = n, Nt(e, r);
  }
  function Nt(e, t) {
    e.lanes |= t;
    var n = e.alternate;
    for (n !== null && (n.lanes |= t), n = e, e = e.return; e !== null; ) e.childLanes |= t, n = e.alternate, n !== null && (n.childLanes |= t), n = e, e = e.return;
    return n.tag === 3 ? n.stateNode : null;
  }
  var $t = !1;
  function Ai(e) {
    e.updateQueue = { baseState: e.memoizedState, firstBaseUpdate: null, lastBaseUpdate: null, shared: { pending: null, interleaved: null, lanes: 0 }, effects: null };
  }
  function hu(e, t) {
    e = e.updateQueue, t.updateQueue === e && (t.updateQueue = { baseState: e.baseState, firstBaseUpdate: e.firstBaseUpdate, lastBaseUpdate: e.lastBaseUpdate, shared: e.shared, effects: e.effects });
  }
  function Pt(e, t) {
    return { eventTime: e, lane: t, tag: 0, payload: null, callback: null, next: null };
  }
  function Ht(e, t, n) {
    var r = e.updateQueue;
    if (r === null) return null;
    if (r = r.shared, (le & 2) !== 0) {
      var l = r.pending;
      return l === null ? t.next = t : (t.next = l.next, l.next = t), r.pending = t, Nt(e, n);
    }
    return l = r.interleaved, l === null ? (t.next = t, Oi(r)) : (t.next = l.next, l.next = t), r.interleaved = t, Nt(e, n);
  }
  function ll(e, t, n) {
    if (t = t.updateQueue, t !== null && (t = t.shared, (n & 4194240) !== 0)) {
      var r = t.lanes;
      r &= e.pendingLanes, n |= r, t.lanes = n, Zl(e, n);
    }
  }
  function vu(e, t) {
    var n = e.updateQueue, r = e.alternate;
    if (r !== null && (r = r.updateQueue, n === r)) {
      var l = null, i = null;
      if (n = n.firstBaseUpdate, n !== null) {
        do {
          var o = { eventTime: n.eventTime, lane: n.lane, tag: n.tag, payload: n.payload, callback: n.callback, next: null };
          i === null ? l = i = o : i = i.next = o, n = n.next;
        } while (n !== null);
        i === null ? l = i = t : i = i.next = t;
      } else l = i = t;
      n = { baseState: r.baseState, firstBaseUpdate: l, lastBaseUpdate: i, shared: r.shared, effects: r.effects }, e.updateQueue = n;
      return;
    }
    e = n.lastBaseUpdate, e === null ? n.firstBaseUpdate = t : e.next = t, n.lastBaseUpdate = t;
  }
  function il(e, t, n, r) {
    var l = e.updateQueue;
    $t = !1;
    var i = l.firstBaseUpdate, o = l.lastBaseUpdate, u = l.shared.pending;
    if (u !== null) {
      l.shared.pending = null;
      var d = u, y = d.next;
      d.next = null, o === null ? i = y : o.next = y, o = d;
      var j = e.alternate;
      j !== null && (j = j.updateQueue, u = j.lastBaseUpdate, u !== o && (u === null ? j.firstBaseUpdate = y : u.next = y, j.lastBaseUpdate = d));
    }
    if (i !== null) {
      var N = l.baseState;
      o = 0, j = y = d = null, u = i;
      do {
        var C = u.lane, I = u.eventTime;
        if ((r & C) === C) {
          j !== null && (j = j.next = {
            eventTime: I,
            lane: 0,
            tag: u.tag,
            payload: u.payload,
            callback: u.callback,
            next: null
          });
          e: {
            var U = e, V = u;
            switch (C = t, I = n, V.tag) {
              case 1:
                if (U = V.payload, typeof U == "function") {
                  N = U.call(I, N, C);
                  break e;
                }
                N = U;
                break e;
              case 3:
                U.flags = U.flags & -65537 | 128;
              case 0:
                if (U = V.payload, C = typeof U == "function" ? U.call(I, N, C) : U, C == null) break e;
                N = O({}, N, C);
                break e;
              case 2:
                $t = !0;
            }
          }
          u.callback !== null && u.lane !== 0 && (e.flags |= 64, C = l.effects, C === null ? l.effects = [u] : C.push(u));
        } else I = { eventTime: I, lane: C, tag: u.tag, payload: u.payload, callback: u.callback, next: null }, j === null ? (y = j = I, d = N) : j = j.next = I, o |= C;
        if (u = u.next, u === null) {
          if (u = l.shared.pending, u === null) break;
          C = u, u = C.next, C.next = null, l.lastBaseUpdate = C, l.shared.pending = null;
        }
      } while (!0);
      if (j === null && (d = N), l.baseState = d, l.firstBaseUpdate = y, l.lastBaseUpdate = j, t = l.shared.interleaved, t !== null) {
        l = t;
        do
          o |= l.lane, l = l.next;
        while (l !== t);
      } else i === null && (l.shared.lanes = 0);
      ln |= o, e.lanes = o, e.memoizedState = N;
    }
  }
  function gu(e, t, n) {
    if (e = t.effects, t.effects = null, e !== null) for (t = 0; t < e.length; t++) {
      var r = e[t], l = r.callback;
      if (l !== null) {
        if (r.callback = null, r = n, typeof l != "function") throw Error(a(191, l));
        l.call(r);
      }
    }
  }
  var ur = {}, xt = Ft(ur), ar = Ft(ur), cr = Ft(ur);
  function nn(e) {
    if (e === ur) throw Error(a(174));
    return e;
  }
  function Fi(e, t) {
    switch (me(cr, t), me(ar, e), me(xt, ur), e = t.nodeType, e) {
      case 9:
      case 11:
        t = (t = t.documentElement) ? t.namespaceURI : Ul(null, "");
        break;
      default:
        e = e === 8 ? t.parentNode : t, t = e.namespaceURI || null, e = e.tagName, t = Ul(t, e);
    }
    ve(xt), me(xt, t);
  }
  function Ln() {
    ve(xt), ve(ar), ve(cr);
  }
  function yu(e) {
    nn(cr.current);
    var t = nn(xt.current), n = Ul(t, e.type);
    t !== n && (me(ar, e), me(xt, n));
  }
  function Ui(e) {
    ar.current === e && (ve(xt), ve(ar));
  }
  var ke = Ft(0);
  function ol(e) {
    for (var t = e; t !== null; ) {
      if (t.tag === 13) {
        var n = t.memoizedState;
        if (n !== null && (n = n.dehydrated, n === null || n.data === "$?" || n.data === "$!")) return t;
      } else if (t.tag === 19 && t.memoizedProps.revealOrder !== void 0) {
        if ((t.flags & 128) !== 0) return t;
      } else if (t.child !== null) {
        t.child.return = t, t = t.child;
        continue;
      }
      if (t === e) break;
      for (; t.sibling === null; ) {
        if (t.return === null || t.return === e) return null;
        t = t.return;
      }
      t.sibling.return = t.return, t = t.sibling;
    }
    return null;
  }
  var Vi = [];
  function $i() {
    for (var e = 0; e < Vi.length; e++) Vi[e]._workInProgressVersionPrimary = null;
    Vi.length = 0;
  }
  var sl = X.ReactCurrentDispatcher, Hi = X.ReactCurrentBatchConfig, rn = 0, Se = null, Re = null, ze = null, ul = !1, dr = !1, fr = 0, Cd = 0;
  function $e() {
    throw Error(a(321));
  }
  function Bi(e, t) {
    if (t === null) return !1;
    for (var n = 0; n < t.length && n < e.length; n++) if (!ft(e[n], t[n])) return !1;
    return !0;
  }
  function Wi(e, t, n, r, l, i) {
    if (rn = i, Se = t, t.memoizedState = null, t.updateQueue = null, t.lanes = 0, sl.current = e === null || e.memoizedState === null ? Pd : Td, e = n(r, l), dr) {
      i = 0;
      do {
        if (dr = !1, fr = 0, 25 <= i) throw Error(a(301));
        i += 1, ze = Re = null, t.updateQueue = null, sl.current = Ld, e = n(r, l);
      } while (dr);
    }
    if (sl.current = dl, t = Re !== null && Re.next !== null, rn = 0, ze = Re = Se = null, ul = !1, t) throw Error(a(300));
    return e;
  }
  function Qi() {
    var e = fr !== 0;
    return fr = 0, e;
  }
  function kt() {
    var e = { memoizedState: null, baseState: null, baseQueue: null, queue: null, next: null };
    return ze === null ? Se.memoizedState = ze = e : ze = ze.next = e, ze;
  }
  function ut() {
    if (Re === null) {
      var e = Se.alternate;
      e = e !== null ? e.memoizedState : null;
    } else e = Re.next;
    var t = ze === null ? Se.memoizedState : ze.next;
    if (t !== null) ze = t, Re = e;
    else {
      if (e === null) throw Error(a(310));
      Re = e, e = { memoizedState: Re.memoizedState, baseState: Re.baseState, baseQueue: Re.baseQueue, queue: Re.queue, next: null }, ze === null ? Se.memoizedState = ze = e : ze = ze.next = e;
    }
    return ze;
  }
  function pr(e, t) {
    return typeof t == "function" ? t(e) : t;
  }
  function Gi(e) {
    var t = ut(), n = t.queue;
    if (n === null) throw Error(a(311));
    n.lastRenderedReducer = e;
    var r = Re, l = r.baseQueue, i = n.pending;
    if (i !== null) {
      if (l !== null) {
        var o = l.next;
        l.next = i.next, i.next = o;
      }
      r.baseQueue = l = i, n.pending = null;
    }
    if (l !== null) {
      i = l.next, r = r.baseState;
      var u = o = null, d = null, y = i;
      do {
        var j = y.lane;
        if ((rn & j) === j) d !== null && (d = d.next = { lane: 0, action: y.action, hasEagerState: y.hasEagerState, eagerState: y.eagerState, next: null }), r = y.hasEagerState ? y.eagerState : e(r, y.action);
        else {
          var N = {
            lane: j,
            action: y.action,
            hasEagerState: y.hasEagerState,
            eagerState: y.eagerState,
            next: null
          };
          d === null ? (u = d = N, o = r) : d = d.next = N, Se.lanes |= j, ln |= j;
        }
        y = y.next;
      } while (y !== null && y !== i);
      d === null ? o = r : d.next = u, ft(r, t.memoizedState) || (Xe = !0), t.memoizedState = r, t.baseState = o, t.baseQueue = d, n.lastRenderedState = r;
    }
    if (e = n.interleaved, e !== null) {
      l = e;
      do
        i = l.lane, Se.lanes |= i, ln |= i, l = l.next;
      while (l !== e);
    } else l === null && (n.lanes = 0);
    return [t.memoizedState, n.dispatch];
  }
  function Ki(e) {
    var t = ut(), n = t.queue;
    if (n === null) throw Error(a(311));
    n.lastRenderedReducer = e;
    var r = n.dispatch, l = n.pending, i = t.memoizedState;
    if (l !== null) {
      n.pending = null;
      var o = l = l.next;
      do
        i = e(i, o.action), o = o.next;
      while (o !== l);
      ft(i, t.memoizedState) || (Xe = !0), t.memoizedState = i, t.baseQueue === null && (t.baseState = i), n.lastRenderedState = i;
    }
    return [i, r];
  }
  function wu() {
  }
  function xu(e, t) {
    var n = Se, r = ut(), l = t(), i = !ft(r.memoizedState, l);
    if (i && (r.memoizedState = l, Xe = !0), r = r.queue, Yi(_u.bind(null, n, r, e), [e]), r.getSnapshot !== t || i || ze !== null && ze.memoizedState.tag & 1) {
      if (n.flags |= 2048, mr(9, Su.bind(null, n, r, l, t), void 0, null), Ie === null) throw Error(a(349));
      (rn & 30) !== 0 || ku(n, t, l);
    }
    return l;
  }
  function ku(e, t, n) {
    e.flags |= 16384, e = { getSnapshot: t, value: n }, t = Se.updateQueue, t === null ? (t = { lastEffect: null, stores: null }, Se.updateQueue = t, t.stores = [e]) : (n = t.stores, n === null ? t.stores = [e] : n.push(e));
  }
  function Su(e, t, n, r) {
    t.value = n, t.getSnapshot = r, Cu(t) && ju(e);
  }
  function _u(e, t, n) {
    return n(function() {
      Cu(t) && ju(e);
    });
  }
  function Cu(e) {
    var t = e.getSnapshot;
    e = e.value;
    try {
      var n = t();
      return !ft(e, n);
    } catch {
      return !0;
    }
  }
  function ju(e) {
    var t = Nt(e, 1);
    t !== null && gt(t, e, 1, -1);
  }
  function Eu(e) {
    var t = kt();
    return typeof e == "function" && (e = e()), t.memoizedState = t.baseState = e, e = { pending: null, interleaved: null, lanes: 0, dispatch: null, lastRenderedReducer: pr, lastRenderedState: e }, t.queue = e, e = e.dispatch = Nd.bind(null, Se, e), [t.memoizedState, e];
  }
  function mr(e, t, n, r) {
    return e = { tag: e, create: t, destroy: n, deps: r, next: null }, t = Se.updateQueue, t === null ? (t = { lastEffect: null, stores: null }, Se.updateQueue = t, t.lastEffect = e.next = e) : (n = t.lastEffect, n === null ? t.lastEffect = e.next = e : (r = n.next, n.next = e, e.next = r, t.lastEffect = e)), e;
  }
  function Nu() {
    return ut().memoizedState;
  }
  function al(e, t, n, r) {
    var l = kt();
    Se.flags |= e, l.memoizedState = mr(1 | t, n, void 0, r === void 0 ? null : r);
  }
  function cl(e, t, n, r) {
    var l = ut();
    r = r === void 0 ? null : r;
    var i = void 0;
    if (Re !== null) {
      var o = Re.memoizedState;
      if (i = o.destroy, r !== null && Bi(r, o.deps)) {
        l.memoizedState = mr(t, n, i, r);
        return;
      }
    }
    Se.flags |= e, l.memoizedState = mr(1 | t, n, i, r);
  }
  function Pu(e, t) {
    return al(8390656, 8, e, t);
  }
  function Yi(e, t) {
    return cl(2048, 8, e, t);
  }
  function Tu(e, t) {
    return cl(4, 2, e, t);
  }
  function Lu(e, t) {
    return cl(4, 4, e, t);
  }
  function Ru(e, t) {
    if (typeof t == "function") return e = e(), t(e), function() {
      t(null);
    };
    if (t != null) return e = e(), t.current = e, function() {
      t.current = null;
    };
  }
  function Mu(e, t, n) {
    return n = n != null ? n.concat([e]) : null, cl(4, 4, Ru.bind(null, t, e), n);
  }
  function qi() {
  }
  function zu(e, t) {
    var n = ut();
    t = t === void 0 ? null : t;
    var r = n.memoizedState;
    return r !== null && t !== null && Bi(t, r[1]) ? r[0] : (n.memoizedState = [e, t], e);
  }
  function Iu(e, t) {
    var n = ut();
    t = t === void 0 ? null : t;
    var r = n.memoizedState;
    return r !== null && t !== null && Bi(t, r[1]) ? r[0] : (e = e(), n.memoizedState = [e, t], e);
  }
  function Du(e, t, n) {
    return (rn & 21) === 0 ? (e.baseState && (e.baseState = !1, Xe = !0), e.memoizedState = n) : (ft(n, t) || (n = ds(), Se.lanes |= n, ln |= n, e.baseState = !0), t);
  }
  function jd(e, t) {
    var n = de;
    de = n !== 0 && 4 > n ? n : 4, e(!0);
    var r = Hi.transition;
    Hi.transition = {};
    try {
      e(!1), t();
    } finally {
      de = n, Hi.transition = r;
    }
  }
  function Ou() {
    return ut().memoizedState;
  }
  function Ed(e, t, n) {
    var r = Gt(e);
    if (n = { lane: r, action: n, hasEagerState: !1, eagerState: null, next: null }, Au(e)) Fu(t, n);
    else if (n = mu(e, t, n, r), n !== null) {
      var l = Ge();
      gt(n, e, r, l), Uu(n, t, r);
    }
  }
  function Nd(e, t, n) {
    var r = Gt(e), l = { lane: r, action: n, hasEagerState: !1, eagerState: null, next: null };
    if (Au(e)) Fu(t, l);
    else {
      var i = e.alternate;
      if (e.lanes === 0 && (i === null || i.lanes === 0) && (i = t.lastRenderedReducer, i !== null)) try {
        var o = t.lastRenderedState, u = i(o, n);
        if (l.hasEagerState = !0, l.eagerState = u, ft(u, o)) {
          var d = t.interleaved;
          d === null ? (l.next = l, Oi(t)) : (l.next = d.next, d.next = l), t.interleaved = l;
          return;
        }
      } catch {
      } finally {
      }
      n = mu(e, t, l, r), n !== null && (l = Ge(), gt(n, e, r, l), Uu(n, t, r));
    }
  }
  function Au(e) {
    var t = e.alternate;
    return e === Se || t !== null && t === Se;
  }
  function Fu(e, t) {
    dr = ul = !0;
    var n = e.pending;
    n === null ? t.next = t : (t.next = n.next, n.next = t), e.pending = t;
  }
  function Uu(e, t, n) {
    if ((n & 4194240) !== 0) {
      var r = t.lanes;
      r &= e.pendingLanes, n |= r, t.lanes = n, Zl(e, n);
    }
  }
  var dl = { readContext: st, useCallback: $e, useContext: $e, useEffect: $e, useImperativeHandle: $e, useInsertionEffect: $e, useLayoutEffect: $e, useMemo: $e, useReducer: $e, useRef: $e, useState: $e, useDebugValue: $e, useDeferredValue: $e, useTransition: $e, useMutableSource: $e, useSyncExternalStore: $e, useId: $e, unstable_isNewReconciler: !1 }, Pd = { readContext: st, useCallback: function(e, t) {
    return kt().memoizedState = [e, t === void 0 ? null : t], e;
  }, useContext: st, useEffect: Pu, useImperativeHandle: function(e, t, n) {
    return n = n != null ? n.concat([e]) : null, al(
      4194308,
      4,
      Ru.bind(null, t, e),
      n
    );
  }, useLayoutEffect: function(e, t) {
    return al(4194308, 4, e, t);
  }, useInsertionEffect: function(e, t) {
    return al(4, 2, e, t);
  }, useMemo: function(e, t) {
    var n = kt();
    return t = t === void 0 ? null : t, e = e(), n.memoizedState = [e, t], e;
  }, useReducer: function(e, t, n) {
    var r = kt();
    return t = n !== void 0 ? n(t) : t, r.memoizedState = r.baseState = t, e = { pending: null, interleaved: null, lanes: 0, dispatch: null, lastRenderedReducer: e, lastRenderedState: t }, r.queue = e, e = e.dispatch = Ed.bind(null, Se, e), [r.memoizedState, e];
  }, useRef: function(e) {
    var t = kt();
    return e = { current: e }, t.memoizedState = e;
  }, useState: Eu, useDebugValue: qi, useDeferredValue: function(e) {
    return kt().memoizedState = e;
  }, useTransition: function() {
    var e = Eu(!1), t = e[0];
    return e = jd.bind(null, e[1]), kt().memoizedState = e, [t, e];
  }, useMutableSource: function() {
  }, useSyncExternalStore: function(e, t, n) {
    var r = Se, l = kt();
    if (we) {
      if (n === void 0) throw Error(a(407));
      n = n();
    } else {
      if (n = t(), Ie === null) throw Error(a(349));
      (rn & 30) !== 0 || ku(r, t, n);
    }
    l.memoizedState = n;
    var i = { value: n, getSnapshot: t };
    return l.queue = i, Pu(_u.bind(
      null,
      r,
      i,
      e
    ), [e]), r.flags |= 2048, mr(9, Su.bind(null, r, i, n, t), void 0, null), n;
  }, useId: function() {
    var e = kt(), t = Ie.identifierPrefix;
    if (we) {
      var n = Et, r = jt;
      n = (r & ~(1 << 32 - dt(r) - 1)).toString(32) + n, t = ":" + t + "R" + n, n = fr++, 0 < n && (t += "H" + n.toString(32)), t += ":";
    } else n = Cd++, t = ":" + t + "r" + n.toString(32) + ":";
    return e.memoizedState = t;
  }, unstable_isNewReconciler: !1 }, Td = {
    readContext: st,
    useCallback: zu,
    useContext: st,
    useEffect: Yi,
    useImperativeHandle: Mu,
    useInsertionEffect: Tu,
    useLayoutEffect: Lu,
    useMemo: Iu,
    useReducer: Gi,
    useRef: Nu,
    useState: function() {
      return Gi(pr);
    },
    useDebugValue: qi,
    useDeferredValue: function(e) {
      var t = ut();
      return Du(t, Re.memoizedState, e);
    },
    useTransition: function() {
      var e = Gi(pr)[0], t = ut().memoizedState;
      return [e, t];
    },
    useMutableSource: wu,
    useSyncExternalStore: xu,
    useId: Ou,
    unstable_isNewReconciler: !1
  }, Ld = { readContext: st, useCallback: zu, useContext: st, useEffect: Yi, useImperativeHandle: Mu, useInsertionEffect: Tu, useLayoutEffect: Lu, useMemo: Iu, useReducer: Ki, useRef: Nu, useState: function() {
    return Ki(pr);
  }, useDebugValue: qi, useDeferredValue: function(e) {
    var t = ut();
    return Re === null ? t.memoizedState = e : Du(t, Re.memoizedState, e);
  }, useTransition: function() {
    var e = Ki(pr)[0], t = ut().memoizedState;
    return [e, t];
  }, useMutableSource: wu, useSyncExternalStore: xu, useId: Ou, unstable_isNewReconciler: !1 };
  function mt(e, t) {
    if (e && e.defaultProps) {
      t = O({}, t), e = e.defaultProps;
      for (var n in e) t[n] === void 0 && (t[n] = e[n]);
      return t;
    }
    return t;
  }
  function Xi(e, t, n, r) {
    t = e.memoizedState, n = n(r, t), n = n == null ? t : O({}, t, n), e.memoizedState = n, e.lanes === 0 && (e.updateQueue.baseState = n);
  }
  var fl = { isMounted: function(e) {
    return (e = e._reactInternals) ? Xt(e) === e : !1;
  }, enqueueSetState: function(e, t, n) {
    e = e._reactInternals;
    var r = Ge(), l = Gt(e), i = Pt(r, l);
    i.payload = t, n != null && (i.callback = n), t = Ht(e, i, l), t !== null && (gt(t, e, l, r), ll(t, e, l));
  }, enqueueReplaceState: function(e, t, n) {
    e = e._reactInternals;
    var r = Ge(), l = Gt(e), i = Pt(r, l);
    i.tag = 1, i.payload = t, n != null && (i.callback = n), t = Ht(e, i, l), t !== null && (gt(t, e, l, r), ll(t, e, l));
  }, enqueueForceUpdate: function(e, t) {
    e = e._reactInternals;
    var n = Ge(), r = Gt(e), l = Pt(n, r);
    l.tag = 2, t != null && (l.callback = t), t = Ht(e, l, r), t !== null && (gt(t, e, r, n), ll(t, e, r));
  } };
  function Vu(e, t, n, r, l, i, o) {
    return e = e.stateNode, typeof e.shouldComponentUpdate == "function" ? e.shouldComponentUpdate(r, i, o) : t.prototype && t.prototype.isPureReactComponent ? !er(n, r) || !er(l, i) : !0;
  }
  function $u(e, t, n) {
    var r = !1, l = Ut, i = t.contextType;
    return typeof i == "object" && i !== null ? i = st(i) : (l = qe(t) ? Jt : Ve.current, r = t.contextTypes, i = (r = r != null) ? _n(e, l) : Ut), t = new t(n, i), e.memoizedState = t.state !== null && t.state !== void 0 ? t.state : null, t.updater = fl, e.stateNode = t, t._reactInternals = e, r && (e = e.stateNode, e.__reactInternalMemoizedUnmaskedChildContext = l, e.__reactInternalMemoizedMaskedChildContext = i), t;
  }
  function Hu(e, t, n, r) {
    e = t.state, typeof t.componentWillReceiveProps == "function" && t.componentWillReceiveProps(n, r), typeof t.UNSAFE_componentWillReceiveProps == "function" && t.UNSAFE_componentWillReceiveProps(n, r), t.state !== e && fl.enqueueReplaceState(t, t.state, null);
  }
  function Zi(e, t, n, r) {
    var l = e.stateNode;
    l.props = n, l.state = e.memoizedState, l.refs = {}, Ai(e);
    var i = t.contextType;
    typeof i == "object" && i !== null ? l.context = st(i) : (i = qe(t) ? Jt : Ve.current, l.context = _n(e, i)), l.state = e.memoizedState, i = t.getDerivedStateFromProps, typeof i == "function" && (Xi(e, t, i, n), l.state = e.memoizedState), typeof t.getDerivedStateFromProps == "function" || typeof l.getSnapshotBeforeUpdate == "function" || typeof l.UNSAFE_componentWillMount != "function" && typeof l.componentWillMount != "function" || (t = l.state, typeof l.componentWillMount == "function" && l.componentWillMount(), typeof l.UNSAFE_componentWillMount == "function" && l.UNSAFE_componentWillMount(), t !== l.state && fl.enqueueReplaceState(l, l.state, null), il(e, n, l, r), l.state = e.memoizedState), typeof l.componentDidMount == "function" && (e.flags |= 4194308);
  }
  function Rn(e, t) {
    try {
      var n = "", r = t;
      do
        n += re(r), r = r.return;
      while (r);
      var l = n;
    } catch (i) {
      l = `
Error generating stack: ` + i.message + `
` + i.stack;
    }
    return { value: e, source: t, stack: l, digest: null };
  }
  function Ji(e, t, n) {
    return { value: e, source: null, stack: n ?? null, digest: t ?? null };
  }
  function bi(e, t) {
    try {
      console.error(t.value);
    } catch (n) {
      setTimeout(function() {
        throw n;
      });
    }
  }
  var Rd = typeof WeakMap == "function" ? WeakMap : Map;
  function Bu(e, t, n) {
    n = Pt(-1, n), n.tag = 3, n.payload = { element: null };
    var r = t.value;
    return n.callback = function() {
      wl || (wl = !0, ho = r), bi(e, t);
    }, n;
  }
  function Wu(e, t, n) {
    n = Pt(-1, n), n.tag = 3;
    var r = e.type.getDerivedStateFromError;
    if (typeof r == "function") {
      var l = t.value;
      n.payload = function() {
        return r(l);
      }, n.callback = function() {
        bi(e, t);
      };
    }
    var i = e.stateNode;
    return i !== null && typeof i.componentDidCatch == "function" && (n.callback = function() {
      bi(e, t), typeof r != "function" && (Wt === null ? Wt = /* @__PURE__ */ new Set([this]) : Wt.add(this));
      var o = t.stack;
      this.componentDidCatch(t.value, { componentStack: o !== null ? o : "" });
    }), n;
  }
  function Qu(e, t, n) {
    var r = e.pingCache;
    if (r === null) {
      r = e.pingCache = new Rd();
      var l = /* @__PURE__ */ new Set();
      r.set(t, l);
    } else l = r.get(t), l === void 0 && (l = /* @__PURE__ */ new Set(), r.set(t, l));
    l.has(n) || (l.add(n), e = Qd.bind(null, e, t, n), t.then(e, e));
  }
  function Gu(e) {
    do {
      var t;
      if ((t = e.tag === 13) && (t = e.memoizedState, t = t !== null ? t.dehydrated !== null : !0), t) return e;
      e = e.return;
    } while (e !== null);
    return null;
  }
  function Ku(e, t, n, r, l) {
    return (e.mode & 1) === 0 ? (e === t ? e.flags |= 65536 : (e.flags |= 128, n.flags |= 131072, n.flags &= -52805, n.tag === 1 && (n.alternate === null ? n.tag = 17 : (t = Pt(-1, 1), t.tag = 2, Ht(n, t, 1))), n.lanes |= 1), e) : (e.flags |= 65536, e.lanes = l, e);
  }
  var Md = X.ReactCurrentOwner, Xe = !1;
  function Qe(e, t, n, r) {
    t.child = e === null ? pu(t, null, n, r) : Nn(t, e.child, n, r);
  }
  function Yu(e, t, n, r, l) {
    n = n.render;
    var i = t.ref;
    return Tn(t, l), r = Wi(e, t, n, r, i, l), n = Qi(), e !== null && !Xe ? (t.updateQueue = e.updateQueue, t.flags &= -2053, e.lanes &= ~l, Tt(e, t, l)) : (we && n && Ni(t), t.flags |= 1, Qe(e, t, r, l), t.child);
  }
  function qu(e, t, n, r, l) {
    if (e === null) {
      var i = n.type;
      return typeof i == "function" && !So(i) && i.defaultProps === void 0 && n.compare === null && n.defaultProps === void 0 ? (t.tag = 15, t.type = i, Xu(e, t, i, r, l)) : (e = jl(n.type, null, r, t, t.mode, l), e.ref = t.ref, e.return = t, t.child = e);
    }
    if (i = e.child, (e.lanes & l) === 0) {
      var o = i.memoizedProps;
      if (n = n.compare, n = n !== null ? n : er, n(o, r) && e.ref === t.ref) return Tt(e, t, l);
    }
    return t.flags |= 1, e = Yt(i, r), e.ref = t.ref, e.return = t, t.child = e;
  }
  function Xu(e, t, n, r, l) {
    if (e !== null) {
      var i = e.memoizedProps;
      if (er(i, r) && e.ref === t.ref) if (Xe = !1, t.pendingProps = r = i, (e.lanes & l) !== 0) (e.flags & 131072) !== 0 && (Xe = !0);
      else return t.lanes = e.lanes, Tt(e, t, l);
    }
    return eo(e, t, n, r, l);
  }
  function Zu(e, t, n) {
    var r = t.pendingProps, l = r.children, i = e !== null ? e.memoizedState : null;
    if (r.mode === "hidden") if ((t.mode & 1) === 0) t.memoizedState = { baseLanes: 0, cachePool: null, transitions: null }, me(zn, lt), lt |= n;
    else {
      if ((n & 1073741824) === 0) return e = i !== null ? i.baseLanes | n : n, t.lanes = t.childLanes = 1073741824, t.memoizedState = { baseLanes: e, cachePool: null, transitions: null }, t.updateQueue = null, me(zn, lt), lt |= e, null;
      t.memoizedState = { baseLanes: 0, cachePool: null, transitions: null }, r = i !== null ? i.baseLanes : n, me(zn, lt), lt |= r;
    }
    else i !== null ? (r = i.baseLanes | n, t.memoizedState = null) : r = n, me(zn, lt), lt |= r;
    return Qe(e, t, l, n), t.child;
  }
  function Ju(e, t) {
    var n = t.ref;
    (e === null && n !== null || e !== null && e.ref !== n) && (t.flags |= 512, t.flags |= 2097152);
  }
  function eo(e, t, n, r, l) {
    var i = qe(n) ? Jt : Ve.current;
    return i = _n(t, i), Tn(t, l), n = Wi(e, t, n, r, i, l), r = Qi(), e !== null && !Xe ? (t.updateQueue = e.updateQueue, t.flags &= -2053, e.lanes &= ~l, Tt(e, t, l)) : (we && r && Ni(t), t.flags |= 1, Qe(e, t, n, l), t.child);
  }
  function bu(e, t, n, r, l) {
    if (qe(n)) {
      var i = !0;
      Xr(t);
    } else i = !1;
    if (Tn(t, l), t.stateNode === null) ml(e, t), $u(t, n, r), Zi(t, n, r, l), r = !0;
    else if (e === null) {
      var o = t.stateNode, u = t.memoizedProps;
      o.props = u;
      var d = o.context, y = n.contextType;
      typeof y == "object" && y !== null ? y = st(y) : (y = qe(n) ? Jt : Ve.current, y = _n(t, y));
      var j = n.getDerivedStateFromProps, N = typeof j == "function" || typeof o.getSnapshotBeforeUpdate == "function";
      N || typeof o.UNSAFE_componentWillReceiveProps != "function" && typeof o.componentWillReceiveProps != "function" || (u !== r || d !== y) && Hu(t, o, r, y), $t = !1;
      var C = t.memoizedState;
      o.state = C, il(t, r, o, l), d = t.memoizedState, u !== r || C !== d || Ye.current || $t ? (typeof j == "function" && (Xi(t, n, j, r), d = t.memoizedState), (u = $t || Vu(t, n, u, r, C, d, y)) ? (N || typeof o.UNSAFE_componentWillMount != "function" && typeof o.componentWillMount != "function" || (typeof o.componentWillMount == "function" && o.componentWillMount(), typeof o.UNSAFE_componentWillMount == "function" && o.UNSAFE_componentWillMount()), typeof o.componentDidMount == "function" && (t.flags |= 4194308)) : (typeof o.componentDidMount == "function" && (t.flags |= 4194308), t.memoizedProps = r, t.memoizedState = d), o.props = r, o.state = d, o.context = y, r = u) : (typeof o.componentDidMount == "function" && (t.flags |= 4194308), r = !1);
    } else {
      o = t.stateNode, hu(e, t), u = t.memoizedProps, y = t.type === t.elementType ? u : mt(t.type, u), o.props = y, N = t.pendingProps, C = o.context, d = n.contextType, typeof d == "object" && d !== null ? d = st(d) : (d = qe(n) ? Jt : Ve.current, d = _n(t, d));
      var I = n.getDerivedStateFromProps;
      (j = typeof I == "function" || typeof o.getSnapshotBeforeUpdate == "function") || typeof o.UNSAFE_componentWillReceiveProps != "function" && typeof o.componentWillReceiveProps != "function" || (u !== N || C !== d) && Hu(t, o, r, d), $t = !1, C = t.memoizedState, o.state = C, il(t, r, o, l);
      var U = t.memoizedState;
      u !== N || C !== U || Ye.current || $t ? (typeof I == "function" && (Xi(t, n, I, r), U = t.memoizedState), (y = $t || Vu(t, n, y, r, C, U, d) || !1) ? (j || typeof o.UNSAFE_componentWillUpdate != "function" && typeof o.componentWillUpdate != "function" || (typeof o.componentWillUpdate == "function" && o.componentWillUpdate(r, U, d), typeof o.UNSAFE_componentWillUpdate == "function" && o.UNSAFE_componentWillUpdate(r, U, d)), typeof o.componentDidUpdate == "function" && (t.flags |= 4), typeof o.getSnapshotBeforeUpdate == "function" && (t.flags |= 1024)) : (typeof o.componentDidUpdate != "function" || u === e.memoizedProps && C === e.memoizedState || (t.flags |= 4), typeof o.getSnapshotBeforeUpdate != "function" || u === e.memoizedProps && C === e.memoizedState || (t.flags |= 1024), t.memoizedProps = r, t.memoizedState = U), o.props = r, o.state = U, o.context = d, r = y) : (typeof o.componentDidUpdate != "function" || u === e.memoizedProps && C === e.memoizedState || (t.flags |= 4), typeof o.getSnapshotBeforeUpdate != "function" || u === e.memoizedProps && C === e.memoizedState || (t.flags |= 1024), r = !1);
    }
    return to(e, t, n, r, i, l);
  }
  function to(e, t, n, r, l, i) {
    Ju(e, t);
    var o = (t.flags & 128) !== 0;
    if (!r && !o) return l && lu(t, n, !1), Tt(e, t, i);
    r = t.stateNode, Md.current = t;
    var u = o && typeof n.getDerivedStateFromError != "function" ? null : r.render();
    return t.flags |= 1, e !== null && o ? (t.child = Nn(t, e.child, null, i), t.child = Nn(t, null, u, i)) : Qe(e, t, u, i), t.memoizedState = r.state, l && lu(t, n, !0), t.child;
  }
  function ea(e) {
    var t = e.stateNode;
    t.pendingContext ? nu(e, t.pendingContext, t.pendingContext !== t.context) : t.context && nu(e, t.context, !1), Fi(e, t.containerInfo);
  }
  function ta(e, t, n, r, l) {
    return En(), Ri(l), t.flags |= 256, Qe(e, t, n, r), t.child;
  }
  var no = { dehydrated: null, treeContext: null, retryLane: 0 };
  function ro(e) {
    return { baseLanes: e, cachePool: null, transitions: null };
  }
  function na(e, t, n) {
    var r = t.pendingProps, l = ke.current, i = !1, o = (t.flags & 128) !== 0, u;
    if ((u = o) || (u = e !== null && e.memoizedState === null ? !1 : (l & 2) !== 0), u ? (i = !0, t.flags &= -129) : (e === null || e.memoizedState !== null) && (l |= 1), me(ke, l & 1), e === null)
      return Li(t), e = t.memoizedState, e !== null && (e = e.dehydrated, e !== null) ? ((t.mode & 1) === 0 ? t.lanes = 1 : e.data === "$!" ? t.lanes = 8 : t.lanes = 1073741824, null) : (o = r.children, e = r.fallback, i ? (r = t.mode, i = t.child, o = { mode: "hidden", children: o }, (r & 1) === 0 && i !== null ? (i.childLanes = 0, i.pendingProps = o) : i = El(o, r, 0, null), e = an(e, r, n, null), i.return = t, e.return = t, i.sibling = e, t.child = i, t.child.memoizedState = ro(n), t.memoizedState = no, e) : lo(t, o));
    if (l = e.memoizedState, l !== null && (u = l.dehydrated, u !== null)) return zd(e, t, o, r, u, l, n);
    if (i) {
      i = r.fallback, o = t.mode, l = e.child, u = l.sibling;
      var d = { mode: "hidden", children: r.children };
      return (o & 1) === 0 && t.child !== l ? (r = t.child, r.childLanes = 0, r.pendingProps = d, t.deletions = null) : (r = Yt(l, d), r.subtreeFlags = l.subtreeFlags & 14680064), u !== null ? i = Yt(u, i) : (i = an(i, o, n, null), i.flags |= 2), i.return = t, r.return = t, r.sibling = i, t.child = r, r = i, i = t.child, o = e.child.memoizedState, o = o === null ? ro(n) : { baseLanes: o.baseLanes | n, cachePool: null, transitions: o.transitions }, i.memoizedState = o, i.childLanes = e.childLanes & ~n, t.memoizedState = no, r;
    }
    return i = e.child, e = i.sibling, r = Yt(i, { mode: "visible", children: r.children }), (t.mode & 1) === 0 && (r.lanes = n), r.return = t, r.sibling = null, e !== null && (n = t.deletions, n === null ? (t.deletions = [e], t.flags |= 16) : n.push(e)), t.child = r, t.memoizedState = null, r;
  }
  function lo(e, t) {
    return t = El({ mode: "visible", children: t }, e.mode, 0, null), t.return = e, e.child = t;
  }
  function pl(e, t, n, r) {
    return r !== null && Ri(r), Nn(t, e.child, null, n), e = lo(t, t.pendingProps.children), e.flags |= 2, t.memoizedState = null, e;
  }
  function zd(e, t, n, r, l, i, o) {
    if (n)
      return t.flags & 256 ? (t.flags &= -257, r = Ji(Error(a(422))), pl(e, t, o, r)) : t.memoizedState !== null ? (t.child = e.child, t.flags |= 128, null) : (i = r.fallback, l = t.mode, r = El({ mode: "visible", children: r.children }, l, 0, null), i = an(i, l, o, null), i.flags |= 2, r.return = t, i.return = t, r.sibling = i, t.child = r, (t.mode & 1) !== 0 && Nn(t, e.child, null, o), t.child.memoizedState = ro(o), t.memoizedState = no, i);
    if ((t.mode & 1) === 0) return pl(e, t, o, null);
    if (l.data === "$!") {
      if (r = l.nextSibling && l.nextSibling.dataset, r) var u = r.dgst;
      return r = u, i = Error(a(419)), r = Ji(i, r, void 0), pl(e, t, o, r);
    }
    if (u = (o & e.childLanes) !== 0, Xe || u) {
      if (r = Ie, r !== null) {
        switch (o & -o) {
          case 4:
            l = 2;
            break;
          case 16:
            l = 8;
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
            l = 32;
            break;
          case 536870912:
            l = 268435456;
            break;
          default:
            l = 0;
        }
        l = (l & (r.suspendedLanes | o)) !== 0 ? 0 : l, l !== 0 && l !== i.retryLane && (i.retryLane = l, Nt(e, l), gt(r, e, l, -1));
      }
      return ko(), r = Ji(Error(a(421))), pl(e, t, o, r);
    }
    return l.data === "$?" ? (t.flags |= 128, t.child = e.child, t = Gd.bind(null, e), l._reactRetry = t, null) : (e = i.treeContext, rt = At(l.nextSibling), nt = t, we = !0, pt = null, e !== null && (it[ot++] = jt, it[ot++] = Et, it[ot++] = bt, jt = e.id, Et = e.overflow, bt = t), t = lo(t, r.children), t.flags |= 4096, t);
  }
  function ra(e, t, n) {
    e.lanes |= t;
    var r = e.alternate;
    r !== null && (r.lanes |= t), Di(e.return, t, n);
  }
  function io(e, t, n, r, l) {
    var i = e.memoizedState;
    i === null ? e.memoizedState = { isBackwards: t, rendering: null, renderingStartTime: 0, last: r, tail: n, tailMode: l } : (i.isBackwards = t, i.rendering = null, i.renderingStartTime = 0, i.last = r, i.tail = n, i.tailMode = l);
  }
  function la(e, t, n) {
    var r = t.pendingProps, l = r.revealOrder, i = r.tail;
    if (Qe(e, t, r.children, n), r = ke.current, (r & 2) !== 0) r = r & 1 | 2, t.flags |= 128;
    else {
      if (e !== null && (e.flags & 128) !== 0) e: for (e = t.child; e !== null; ) {
        if (e.tag === 13) e.memoizedState !== null && ra(e, n, t);
        else if (e.tag === 19) ra(e, n, t);
        else if (e.child !== null) {
          e.child.return = e, e = e.child;
          continue;
        }
        if (e === t) break e;
        for (; e.sibling === null; ) {
          if (e.return === null || e.return === t) break e;
          e = e.return;
        }
        e.sibling.return = e.return, e = e.sibling;
      }
      r &= 1;
    }
    if (me(ke, r), (t.mode & 1) === 0) t.memoizedState = null;
    else switch (l) {
      case "forwards":
        for (n = t.child, l = null; n !== null; ) e = n.alternate, e !== null && ol(e) === null && (l = n), n = n.sibling;
        n = l, n === null ? (l = t.child, t.child = null) : (l = n.sibling, n.sibling = null), io(t, !1, l, n, i);
        break;
      case "backwards":
        for (n = null, l = t.child, t.child = null; l !== null; ) {
          if (e = l.alternate, e !== null && ol(e) === null) {
            t.child = l;
            break;
          }
          e = l.sibling, l.sibling = n, n = l, l = e;
        }
        io(t, !0, n, null, i);
        break;
      case "together":
        io(t, !1, null, null, void 0);
        break;
      default:
        t.memoizedState = null;
    }
    return t.child;
  }
  function ml(e, t) {
    (t.mode & 1) === 0 && e !== null && (e.alternate = null, t.alternate = null, t.flags |= 2);
  }
  function Tt(e, t, n) {
    if (e !== null && (t.dependencies = e.dependencies), ln |= t.lanes, (n & t.childLanes) === 0) return null;
    if (e !== null && t.child !== e.child) throw Error(a(153));
    if (t.child !== null) {
      for (e = t.child, n = Yt(e, e.pendingProps), t.child = n, n.return = t; e.sibling !== null; ) e = e.sibling, n = n.sibling = Yt(e, e.pendingProps), n.return = t;
      n.sibling = null;
    }
    return t.child;
  }
  function Id(e, t, n) {
    switch (t.tag) {
      case 3:
        ea(t), En();
        break;
      case 5:
        yu(t);
        break;
      case 1:
        qe(t.type) && Xr(t);
        break;
      case 4:
        Fi(t, t.stateNode.containerInfo);
        break;
      case 10:
        var r = t.type._context, l = t.memoizedProps.value;
        me(nl, r._currentValue), r._currentValue = l;
        break;
      case 13:
        if (r = t.memoizedState, r !== null)
          return r.dehydrated !== null ? (me(ke, ke.current & 1), t.flags |= 128, null) : (n & t.child.childLanes) !== 0 ? na(e, t, n) : (me(ke, ke.current & 1), e = Tt(e, t, n), e !== null ? e.sibling : null);
        me(ke, ke.current & 1);
        break;
      case 19:
        if (r = (n & t.childLanes) !== 0, (e.flags & 128) !== 0) {
          if (r) return la(e, t, n);
          t.flags |= 128;
        }
        if (l = t.memoizedState, l !== null && (l.rendering = null, l.tail = null, l.lastEffect = null), me(ke, ke.current), r) break;
        return null;
      case 22:
      case 23:
        return t.lanes = 0, Zu(e, t, n);
    }
    return Tt(e, t, n);
  }
  var ia, oo, oa, sa;
  ia = function(e, t) {
    for (var n = t.child; n !== null; ) {
      if (n.tag === 5 || n.tag === 6) e.appendChild(n.stateNode);
      else if (n.tag !== 4 && n.child !== null) {
        n.child.return = n, n = n.child;
        continue;
      }
      if (n === t) break;
      for (; n.sibling === null; ) {
        if (n.return === null || n.return === t) return;
        n = n.return;
      }
      n.sibling.return = n.return, n = n.sibling;
    }
  }, oo = function() {
  }, oa = function(e, t, n, r) {
    var l = e.memoizedProps;
    if (l !== r) {
      e = t.stateNode, nn(xt.current);
      var i = null;
      switch (n) {
        case "input":
          l = Dl(e, l), r = Dl(e, r), i = [];
          break;
        case "select":
          l = O({}, l, { value: void 0 }), r = O({}, r, { value: void 0 }), i = [];
          break;
        case "textarea":
          l = Fl(e, l), r = Fl(e, r), i = [];
          break;
        default:
          typeof l.onClick != "function" && typeof r.onClick == "function" && (e.onclick = Kr);
      }
      Vl(n, r);
      var o;
      n = null;
      for (y in l) if (!r.hasOwnProperty(y) && l.hasOwnProperty(y) && l[y] != null) if (y === "style") {
        var u = l[y];
        for (o in u) u.hasOwnProperty(o) && (n || (n = {}), n[o] = "");
      } else y !== "dangerouslySetInnerHTML" && y !== "children" && y !== "suppressContentEditableWarning" && y !== "suppressHydrationWarning" && y !== "autoFocus" && (S.hasOwnProperty(y) ? i || (i = []) : (i = i || []).push(y, null));
      for (y in r) {
        var d = r[y];
        if (u = l != null ? l[y] : void 0, r.hasOwnProperty(y) && d !== u && (d != null || u != null)) if (y === "style") if (u) {
          for (o in u) !u.hasOwnProperty(o) || d && d.hasOwnProperty(o) || (n || (n = {}), n[o] = "");
          for (o in d) d.hasOwnProperty(o) && u[o] !== d[o] && (n || (n = {}), n[o] = d[o]);
        } else n || (i || (i = []), i.push(
          y,
          n
        )), n = d;
        else y === "dangerouslySetInnerHTML" ? (d = d ? d.__html : void 0, u = u ? u.__html : void 0, d != null && u !== d && (i = i || []).push(y, d)) : y === "children" ? typeof d != "string" && typeof d != "number" || (i = i || []).push(y, "" + d) : y !== "suppressContentEditableWarning" && y !== "suppressHydrationWarning" && (S.hasOwnProperty(y) ? (d != null && y === "onScroll" && he("scroll", e), i || u === d || (i = [])) : (i = i || []).push(y, d));
      }
      n && (i = i || []).push("style", n);
      var y = i;
      (t.updateQueue = y) && (t.flags |= 4);
    }
  }, sa = function(e, t, n, r) {
    n !== r && (t.flags |= 4);
  };
  function hr(e, t) {
    if (!we) switch (e.tailMode) {
      case "hidden":
        t = e.tail;
        for (var n = null; t !== null; ) t.alternate !== null && (n = t), t = t.sibling;
        n === null ? e.tail = null : n.sibling = null;
        break;
      case "collapsed":
        n = e.tail;
        for (var r = null; n !== null; ) n.alternate !== null && (r = n), n = n.sibling;
        r === null ? t || e.tail === null ? e.tail = null : e.tail.sibling = null : r.sibling = null;
    }
  }
  function He(e) {
    var t = e.alternate !== null && e.alternate.child === e.child, n = 0, r = 0;
    if (t) for (var l = e.child; l !== null; ) n |= l.lanes | l.childLanes, r |= l.subtreeFlags & 14680064, r |= l.flags & 14680064, l.return = e, l = l.sibling;
    else for (l = e.child; l !== null; ) n |= l.lanes | l.childLanes, r |= l.subtreeFlags, r |= l.flags, l.return = e, l = l.sibling;
    return e.subtreeFlags |= r, e.childLanes = n, t;
  }
  function Dd(e, t, n) {
    var r = t.pendingProps;
    switch (Pi(t), t.tag) {
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
        return He(t), null;
      case 1:
        return qe(t.type) && qr(), He(t), null;
      case 3:
        return r = t.stateNode, Ln(), ve(Ye), ve(Ve), $i(), r.pendingContext && (r.context = r.pendingContext, r.pendingContext = null), (e === null || e.child === null) && (el(t) ? t.flags |= 4 : e === null || e.memoizedState.isDehydrated && (t.flags & 256) === 0 || (t.flags |= 1024, pt !== null && (yo(pt), pt = null))), oo(e, t), He(t), null;
      case 5:
        Ui(t);
        var l = nn(cr.current);
        if (n = t.type, e !== null && t.stateNode != null) oa(e, t, n, r, l), e.ref !== t.ref && (t.flags |= 512, t.flags |= 2097152);
        else {
          if (!r) {
            if (t.stateNode === null) throw Error(a(166));
            return He(t), null;
          }
          if (e = nn(xt.current), el(t)) {
            r = t.stateNode, n = t.type;
            var i = t.memoizedProps;
            switch (r[wt] = t, r[ir] = i, e = (t.mode & 1) !== 0, n) {
              case "dialog":
                he("cancel", r), he("close", r);
                break;
              case "iframe":
              case "object":
              case "embed":
                he("load", r);
                break;
              case "video":
              case "audio":
                for (l = 0; l < nr.length; l++) he(nr[l], r);
                break;
              case "source":
                he("error", r);
                break;
              case "img":
              case "image":
              case "link":
                he(
                  "error",
                  r
                ), he("load", r);
                break;
              case "details":
                he("toggle", r);
                break;
              case "input":
                $o(r, i), he("invalid", r);
                break;
              case "select":
                r._wrapperState = { wasMultiple: !!i.multiple }, he("invalid", r);
                break;
              case "textarea":
                Wo(r, i), he("invalid", r);
            }
            Vl(n, i), l = null;
            for (var o in i) if (i.hasOwnProperty(o)) {
              var u = i[o];
              o === "children" ? typeof u == "string" ? r.textContent !== u && (i.suppressHydrationWarning !== !0 && Gr(r.textContent, u, e), l = ["children", u]) : typeof u == "number" && r.textContent !== "" + u && (i.suppressHydrationWarning !== !0 && Gr(
                r.textContent,
                u,
                e
              ), l = ["children", "" + u]) : S.hasOwnProperty(o) && u != null && o === "onScroll" && he("scroll", r);
            }
            switch (n) {
              case "input":
                _r(r), Bo(r, i, !0);
                break;
              case "textarea":
                _r(r), Go(r);
                break;
              case "select":
              case "option":
                break;
              default:
                typeof i.onClick == "function" && (r.onclick = Kr);
            }
            r = l, t.updateQueue = r, r !== null && (t.flags |= 4);
          } else {
            o = l.nodeType === 9 ? l : l.ownerDocument, e === "http://www.w3.org/1999/xhtml" && (e = Ko(n)), e === "http://www.w3.org/1999/xhtml" ? n === "script" ? (e = o.createElement("div"), e.innerHTML = "<script><\/script>", e = e.removeChild(e.firstChild)) : typeof r.is == "string" ? e = o.createElement(n, { is: r.is }) : (e = o.createElement(n), n === "select" && (o = e, r.multiple ? o.multiple = !0 : r.size && (o.size = r.size))) : e = o.createElementNS(e, n), e[wt] = t, e[ir] = r, ia(e, t, !1, !1), t.stateNode = e;
            e: {
              switch (o = $l(n, r), n) {
                case "dialog":
                  he("cancel", e), he("close", e), l = r;
                  break;
                case "iframe":
                case "object":
                case "embed":
                  he("load", e), l = r;
                  break;
                case "video":
                case "audio":
                  for (l = 0; l < nr.length; l++) he(nr[l], e);
                  l = r;
                  break;
                case "source":
                  he("error", e), l = r;
                  break;
                case "img":
                case "image":
                case "link":
                  he(
                    "error",
                    e
                  ), he("load", e), l = r;
                  break;
                case "details":
                  he("toggle", e), l = r;
                  break;
                case "input":
                  $o(e, r), l = Dl(e, r), he("invalid", e);
                  break;
                case "option":
                  l = r;
                  break;
                case "select":
                  e._wrapperState = { wasMultiple: !!r.multiple }, l = O({}, r, { value: void 0 }), he("invalid", e);
                  break;
                case "textarea":
                  Wo(e, r), l = Fl(e, r), he("invalid", e);
                  break;
                default:
                  l = r;
              }
              Vl(n, l), u = l;
              for (i in u) if (u.hasOwnProperty(i)) {
                var d = u[i];
                i === "style" ? Xo(e, d) : i === "dangerouslySetInnerHTML" ? (d = d ? d.__html : void 0, d != null && Yo(e, d)) : i === "children" ? typeof d == "string" ? (n !== "textarea" || d !== "") && An(e, d) : typeof d == "number" && An(e, "" + d) : i !== "suppressContentEditableWarning" && i !== "suppressHydrationWarning" && i !== "autoFocus" && (S.hasOwnProperty(i) ? d != null && i === "onScroll" && he("scroll", e) : d != null && _e(e, i, d, o));
              }
              switch (n) {
                case "input":
                  _r(e), Bo(e, r, !1);
                  break;
                case "textarea":
                  _r(e), Go(e);
                  break;
                case "option":
                  r.value != null && e.setAttribute("value", "" + ce(r.value));
                  break;
                case "select":
                  e.multiple = !!r.multiple, i = r.value, i != null ? dn(e, !!r.multiple, i, !1) : r.defaultValue != null && dn(
                    e,
                    !!r.multiple,
                    r.defaultValue,
                    !0
                  );
                  break;
                default:
                  typeof l.onClick == "function" && (e.onclick = Kr);
              }
              switch (n) {
                case "button":
                case "input":
                case "select":
                case "textarea":
                  r = !!r.autoFocus;
                  break e;
                case "img":
                  r = !0;
                  break e;
                default:
                  r = !1;
              }
            }
            r && (t.flags |= 4);
          }
          t.ref !== null && (t.flags |= 512, t.flags |= 2097152);
        }
        return He(t), null;
      case 6:
        if (e && t.stateNode != null) sa(e, t, e.memoizedProps, r);
        else {
          if (typeof r != "string" && t.stateNode === null) throw Error(a(166));
          if (n = nn(cr.current), nn(xt.current), el(t)) {
            if (r = t.stateNode, n = t.memoizedProps, r[wt] = t, (i = r.nodeValue !== n) && (e = nt, e !== null)) switch (e.tag) {
              case 3:
                Gr(r.nodeValue, n, (e.mode & 1) !== 0);
                break;
              case 5:
                e.memoizedProps.suppressHydrationWarning !== !0 && Gr(r.nodeValue, n, (e.mode & 1) !== 0);
            }
            i && (t.flags |= 4);
          } else r = (n.nodeType === 9 ? n : n.ownerDocument).createTextNode(r), r[wt] = t, t.stateNode = r;
        }
        return He(t), null;
      case 13:
        if (ve(ke), r = t.memoizedState, e === null || e.memoizedState !== null && e.memoizedState.dehydrated !== null) {
          if (we && rt !== null && (t.mode & 1) !== 0 && (t.flags & 128) === 0) cu(), En(), t.flags |= 98560, i = !1;
          else if (i = el(t), r !== null && r.dehydrated !== null) {
            if (e === null) {
              if (!i) throw Error(a(318));
              if (i = t.memoizedState, i = i !== null ? i.dehydrated : null, !i) throw Error(a(317));
              i[wt] = t;
            } else En(), (t.flags & 128) === 0 && (t.memoizedState = null), t.flags |= 4;
            He(t), i = !1;
          } else pt !== null && (yo(pt), pt = null), i = !0;
          if (!i) return t.flags & 65536 ? t : null;
        }
        return (t.flags & 128) !== 0 ? (t.lanes = n, t) : (r = r !== null, r !== (e !== null && e.memoizedState !== null) && r && (t.child.flags |= 8192, (t.mode & 1) !== 0 && (e === null || (ke.current & 1) !== 0 ? Me === 0 && (Me = 3) : ko())), t.updateQueue !== null && (t.flags |= 4), He(t), null);
      case 4:
        return Ln(), oo(e, t), e === null && rr(t.stateNode.containerInfo), He(t), null;
      case 10:
        return Ii(t.type._context), He(t), null;
      case 17:
        return qe(t.type) && qr(), He(t), null;
      case 19:
        if (ve(ke), i = t.memoizedState, i === null) return He(t), null;
        if (r = (t.flags & 128) !== 0, o = i.rendering, o === null) if (r) hr(i, !1);
        else {
          if (Me !== 0 || e !== null && (e.flags & 128) !== 0) for (e = t.child; e !== null; ) {
            if (o = ol(e), o !== null) {
              for (t.flags |= 128, hr(i, !1), r = o.updateQueue, r !== null && (t.updateQueue = r, t.flags |= 4), t.subtreeFlags = 0, r = n, n = t.child; n !== null; ) i = n, e = r, i.flags &= 14680066, o = i.alternate, o === null ? (i.childLanes = 0, i.lanes = e, i.child = null, i.subtreeFlags = 0, i.memoizedProps = null, i.memoizedState = null, i.updateQueue = null, i.dependencies = null, i.stateNode = null) : (i.childLanes = o.childLanes, i.lanes = o.lanes, i.child = o.child, i.subtreeFlags = 0, i.deletions = null, i.memoizedProps = o.memoizedProps, i.memoizedState = o.memoizedState, i.updateQueue = o.updateQueue, i.type = o.type, e = o.dependencies, i.dependencies = e === null ? null : { lanes: e.lanes, firstContext: e.firstContext }), n = n.sibling;
              return me(ke, ke.current & 1 | 2), t.child;
            }
            e = e.sibling;
          }
          i.tail !== null && Ee() > In && (t.flags |= 128, r = !0, hr(i, !1), t.lanes = 4194304);
        }
        else {
          if (!r) if (e = ol(o), e !== null) {
            if (t.flags |= 128, r = !0, n = e.updateQueue, n !== null && (t.updateQueue = n, t.flags |= 4), hr(i, !0), i.tail === null && i.tailMode === "hidden" && !o.alternate && !we) return He(t), null;
          } else 2 * Ee() - i.renderingStartTime > In && n !== 1073741824 && (t.flags |= 128, r = !0, hr(i, !1), t.lanes = 4194304);
          i.isBackwards ? (o.sibling = t.child, t.child = o) : (n = i.last, n !== null ? n.sibling = o : t.child = o, i.last = o);
        }
        return i.tail !== null ? (t = i.tail, i.rendering = t, i.tail = t.sibling, i.renderingStartTime = Ee(), t.sibling = null, n = ke.current, me(ke, r ? n & 1 | 2 : n & 1), t) : (He(t), null);
      case 22:
      case 23:
        return xo(), r = t.memoizedState !== null, e !== null && e.memoizedState !== null !== r && (t.flags |= 8192), r && (t.mode & 1) !== 0 ? (lt & 1073741824) !== 0 && (He(t), t.subtreeFlags & 6 && (t.flags |= 8192)) : He(t), null;
      case 24:
        return null;
      case 25:
        return null;
    }
    throw Error(a(156, t.tag));
  }
  function Od(e, t) {
    switch (Pi(t), t.tag) {
      case 1:
        return qe(t.type) && qr(), e = t.flags, e & 65536 ? (t.flags = e & -65537 | 128, t) : null;
      case 3:
        return Ln(), ve(Ye), ve(Ve), $i(), e = t.flags, (e & 65536) !== 0 && (e & 128) === 0 ? (t.flags = e & -65537 | 128, t) : null;
      case 5:
        return Ui(t), null;
      case 13:
        if (ve(ke), e = t.memoizedState, e !== null && e.dehydrated !== null) {
          if (t.alternate === null) throw Error(a(340));
          En();
        }
        return e = t.flags, e & 65536 ? (t.flags = e & -65537 | 128, t) : null;
      case 19:
        return ve(ke), null;
      case 4:
        return Ln(), null;
      case 10:
        return Ii(t.type._context), null;
      case 22:
      case 23:
        return xo(), null;
      case 24:
        return null;
      default:
        return null;
    }
  }
  var hl = !1, Be = !1, Ad = typeof WeakSet == "function" ? WeakSet : Set, A = null;
  function Mn(e, t) {
    var n = e.ref;
    if (n !== null) if (typeof n == "function") try {
      n(null);
    } catch (r) {
      Ce(e, t, r);
    }
    else n.current = null;
  }
  function so(e, t, n) {
    try {
      n();
    } catch (r) {
      Ce(e, t, r);
    }
  }
  var ua = !1;
  function Fd(e, t) {
    if (wi = Dr, e = Vs(), di(e)) {
      if ("selectionStart" in e) var n = { start: e.selectionStart, end: e.selectionEnd };
      else e: {
        n = (n = e.ownerDocument) && n.defaultView || window;
        var r = n.getSelection && n.getSelection();
        if (r && r.rangeCount !== 0) {
          n = r.anchorNode;
          var l = r.anchorOffset, i = r.focusNode;
          r = r.focusOffset;
          try {
            n.nodeType, i.nodeType;
          } catch {
            n = null;
            break e;
          }
          var o = 0, u = -1, d = -1, y = 0, j = 0, N = e, C = null;
          t: for (; ; ) {
            for (var I; N !== n || l !== 0 && N.nodeType !== 3 || (u = o + l), N !== i || r !== 0 && N.nodeType !== 3 || (d = o + r), N.nodeType === 3 && (o += N.nodeValue.length), (I = N.firstChild) !== null; )
              C = N, N = I;
            for (; ; ) {
              if (N === e) break t;
              if (C === n && ++y === l && (u = o), C === i && ++j === r && (d = o), (I = N.nextSibling) !== null) break;
              N = C, C = N.parentNode;
            }
            N = I;
          }
          n = u === -1 || d === -1 ? null : { start: u, end: d };
        } else n = null;
      }
      n = n || { start: 0, end: 0 };
    } else n = null;
    for (xi = { focusedElem: e, selectionRange: n }, Dr = !1, A = t; A !== null; ) if (t = A, e = t.child, (t.subtreeFlags & 1028) !== 0 && e !== null) e.return = t, A = e;
    else for (; A !== null; ) {
      t = A;
      try {
        var U = t.alternate;
        if ((t.flags & 1024) !== 0) switch (t.tag) {
          case 0:
          case 11:
          case 15:
            break;
          case 1:
            if (U !== null) {
              var V = U.memoizedProps, Ne = U.memoizedState, h = t.stateNode, p = h.getSnapshotBeforeUpdate(t.elementType === t.type ? V : mt(t.type, V), Ne);
              h.__reactInternalSnapshotBeforeUpdate = p;
            }
            break;
          case 3:
            var v = t.stateNode.containerInfo;
            v.nodeType === 1 ? v.textContent = "" : v.nodeType === 9 && v.documentElement && v.removeChild(v.documentElement);
            break;
          case 5:
          case 6:
          case 4:
          case 17:
            break;
          default:
            throw Error(a(163));
        }
      } catch (T) {
        Ce(t, t.return, T);
      }
      if (e = t.sibling, e !== null) {
        e.return = t.return, A = e;
        break;
      }
      A = t.return;
    }
    return U = ua, ua = !1, U;
  }
  function vr(e, t, n) {
    var r = t.updateQueue;
    if (r = r !== null ? r.lastEffect : null, r !== null) {
      var l = r = r.next;
      do {
        if ((l.tag & e) === e) {
          var i = l.destroy;
          l.destroy = void 0, i !== void 0 && so(t, n, i);
        }
        l = l.next;
      } while (l !== r);
    }
  }
  function vl(e, t) {
    if (t = t.updateQueue, t = t !== null ? t.lastEffect : null, t !== null) {
      var n = t = t.next;
      do {
        if ((n.tag & e) === e) {
          var r = n.create;
          n.destroy = r();
        }
        n = n.next;
      } while (n !== t);
    }
  }
  function uo(e) {
    var t = e.ref;
    if (t !== null) {
      var n = e.stateNode;
      switch (e.tag) {
        case 5:
          e = n;
          break;
        default:
          e = n;
      }
      typeof t == "function" ? t(e) : t.current = e;
    }
  }
  function aa(e) {
    var t = e.alternate;
    t !== null && (e.alternate = null, aa(t)), e.child = null, e.deletions = null, e.sibling = null, e.tag === 5 && (t = e.stateNode, t !== null && (delete t[wt], delete t[ir], delete t[Ci], delete t[xd], delete t[kd])), e.stateNode = null, e.return = null, e.dependencies = null, e.memoizedProps = null, e.memoizedState = null, e.pendingProps = null, e.stateNode = null, e.updateQueue = null;
  }
  function ca(e) {
    return e.tag === 5 || e.tag === 3 || e.tag === 4;
  }
  function da(e) {
    e: for (; ; ) {
      for (; e.sibling === null; ) {
        if (e.return === null || ca(e.return)) return null;
        e = e.return;
      }
      for (e.sibling.return = e.return, e = e.sibling; e.tag !== 5 && e.tag !== 6 && e.tag !== 18; ) {
        if (e.flags & 2 || e.child === null || e.tag === 4) continue e;
        e.child.return = e, e = e.child;
      }
      if (!(e.flags & 2)) return e.stateNode;
    }
  }
  function ao(e, t, n) {
    var r = e.tag;
    if (r === 5 || r === 6) e = e.stateNode, t ? n.nodeType === 8 ? n.parentNode.insertBefore(e, t) : n.insertBefore(e, t) : (n.nodeType === 8 ? (t = n.parentNode, t.insertBefore(e, n)) : (t = n, t.appendChild(e)), n = n._reactRootContainer, n != null || t.onclick !== null || (t.onclick = Kr));
    else if (r !== 4 && (e = e.child, e !== null)) for (ao(e, t, n), e = e.sibling; e !== null; ) ao(e, t, n), e = e.sibling;
  }
  function co(e, t, n) {
    var r = e.tag;
    if (r === 5 || r === 6) e = e.stateNode, t ? n.insertBefore(e, t) : n.appendChild(e);
    else if (r !== 4 && (e = e.child, e !== null)) for (co(e, t, n), e = e.sibling; e !== null; ) co(e, t, n), e = e.sibling;
  }
  var Fe = null, ht = !1;
  function Bt(e, t, n) {
    for (n = n.child; n !== null; ) fa(e, t, n), n = n.sibling;
  }
  function fa(e, t, n) {
    if (yt && typeof yt.onCommitFiberUnmount == "function") try {
      yt.onCommitFiberUnmount(Tr, n);
    } catch {
    }
    switch (n.tag) {
      case 5:
        Be || Mn(n, t);
      case 6:
        var r = Fe, l = ht;
        Fe = null, Bt(e, t, n), Fe = r, ht = l, Fe !== null && (ht ? (e = Fe, n = n.stateNode, e.nodeType === 8 ? e.parentNode.removeChild(n) : e.removeChild(n)) : Fe.removeChild(n.stateNode));
        break;
      case 18:
        Fe !== null && (ht ? (e = Fe, n = n.stateNode, e.nodeType === 8 ? _i(e.parentNode, n) : e.nodeType === 1 && _i(e, n), Yn(e)) : _i(Fe, n.stateNode));
        break;
      case 4:
        r = Fe, l = ht, Fe = n.stateNode.containerInfo, ht = !0, Bt(e, t, n), Fe = r, ht = l;
        break;
      case 0:
      case 11:
      case 14:
      case 15:
        if (!Be && (r = n.updateQueue, r !== null && (r = r.lastEffect, r !== null))) {
          l = r = r.next;
          do {
            var i = l, o = i.destroy;
            i = i.tag, o !== void 0 && ((i & 2) !== 0 || (i & 4) !== 0) && so(n, t, o), l = l.next;
          } while (l !== r);
        }
        Bt(e, t, n);
        break;
      case 1:
        if (!Be && (Mn(n, t), r = n.stateNode, typeof r.componentWillUnmount == "function")) try {
          r.props = n.memoizedProps, r.state = n.memoizedState, r.componentWillUnmount();
        } catch (u) {
          Ce(n, t, u);
        }
        Bt(e, t, n);
        break;
      case 21:
        Bt(e, t, n);
        break;
      case 22:
        n.mode & 1 ? (Be = (r = Be) || n.memoizedState !== null, Bt(e, t, n), Be = r) : Bt(e, t, n);
        break;
      default:
        Bt(e, t, n);
    }
  }
  function pa(e) {
    var t = e.updateQueue;
    if (t !== null) {
      e.updateQueue = null;
      var n = e.stateNode;
      n === null && (n = e.stateNode = new Ad()), t.forEach(function(r) {
        var l = Kd.bind(null, e, r);
        n.has(r) || (n.add(r), r.then(l, l));
      });
    }
  }
  function vt(e, t) {
    var n = t.deletions;
    if (n !== null) for (var r = 0; r < n.length; r++) {
      var l = n[r];
      try {
        var i = e, o = t, u = o;
        e: for (; u !== null; ) {
          switch (u.tag) {
            case 5:
              Fe = u.stateNode, ht = !1;
              break e;
            case 3:
              Fe = u.stateNode.containerInfo, ht = !0;
              break e;
            case 4:
              Fe = u.stateNode.containerInfo, ht = !0;
              break e;
          }
          u = u.return;
        }
        if (Fe === null) throw Error(a(160));
        fa(i, o, l), Fe = null, ht = !1;
        var d = l.alternate;
        d !== null && (d.return = null), l.return = null;
      } catch (y) {
        Ce(l, t, y);
      }
    }
    if (t.subtreeFlags & 12854) for (t = t.child; t !== null; ) ma(t, e), t = t.sibling;
  }
  function ma(e, t) {
    var n = e.alternate, r = e.flags;
    switch (e.tag) {
      case 0:
      case 11:
      case 14:
      case 15:
        if (vt(t, e), St(e), r & 4) {
          try {
            vr(3, e, e.return), vl(3, e);
          } catch (V) {
            Ce(e, e.return, V);
          }
          try {
            vr(5, e, e.return);
          } catch (V) {
            Ce(e, e.return, V);
          }
        }
        break;
      case 1:
        vt(t, e), St(e), r & 512 && n !== null && Mn(n, n.return);
        break;
      case 5:
        if (vt(t, e), St(e), r & 512 && n !== null && Mn(n, n.return), e.flags & 32) {
          var l = e.stateNode;
          try {
            An(l, "");
          } catch (V) {
            Ce(e, e.return, V);
          }
        }
        if (r & 4 && (l = e.stateNode, l != null)) {
          var i = e.memoizedProps, o = n !== null ? n.memoizedProps : i, u = e.type, d = e.updateQueue;
          if (e.updateQueue = null, d !== null) try {
            u === "input" && i.type === "radio" && i.name != null && Ho(l, i), $l(u, o);
            var y = $l(u, i);
            for (o = 0; o < d.length; o += 2) {
              var j = d[o], N = d[o + 1];
              j === "style" ? Xo(l, N) : j === "dangerouslySetInnerHTML" ? Yo(l, N) : j === "children" ? An(l, N) : _e(l, j, N, y);
            }
            switch (u) {
              case "input":
                Ol(l, i);
                break;
              case "textarea":
                Qo(l, i);
                break;
              case "select":
                var C = l._wrapperState.wasMultiple;
                l._wrapperState.wasMultiple = !!i.multiple;
                var I = i.value;
                I != null ? dn(l, !!i.multiple, I, !1) : C !== !!i.multiple && (i.defaultValue != null ? dn(
                  l,
                  !!i.multiple,
                  i.defaultValue,
                  !0
                ) : dn(l, !!i.multiple, i.multiple ? [] : "", !1));
            }
            l[ir] = i;
          } catch (V) {
            Ce(e, e.return, V);
          }
        }
        break;
      case 6:
        if (vt(t, e), St(e), r & 4) {
          if (e.stateNode === null) throw Error(a(162));
          l = e.stateNode, i = e.memoizedProps;
          try {
            l.nodeValue = i;
          } catch (V) {
            Ce(e, e.return, V);
          }
        }
        break;
      case 3:
        if (vt(t, e), St(e), r & 4 && n !== null && n.memoizedState.isDehydrated) try {
          Yn(t.containerInfo);
        } catch (V) {
          Ce(e, e.return, V);
        }
        break;
      case 4:
        vt(t, e), St(e);
        break;
      case 13:
        vt(t, e), St(e), l = e.child, l.flags & 8192 && (i = l.memoizedState !== null, l.stateNode.isHidden = i, !i || l.alternate !== null && l.alternate.memoizedState !== null || (mo = Ee())), r & 4 && pa(e);
        break;
      case 22:
        if (j = n !== null && n.memoizedState !== null, e.mode & 1 ? (Be = (y = Be) || j, vt(t, e), Be = y) : vt(t, e), St(e), r & 8192) {
          if (y = e.memoizedState !== null, (e.stateNode.isHidden = y) && !j && (e.mode & 1) !== 0) for (A = e, j = e.child; j !== null; ) {
            for (N = A = j; A !== null; ) {
              switch (C = A, I = C.child, C.tag) {
                case 0:
                case 11:
                case 14:
                case 15:
                  vr(4, C, C.return);
                  break;
                case 1:
                  Mn(C, C.return);
                  var U = C.stateNode;
                  if (typeof U.componentWillUnmount == "function") {
                    r = C, n = C.return;
                    try {
                      t = r, U.props = t.memoizedProps, U.state = t.memoizedState, U.componentWillUnmount();
                    } catch (V) {
                      Ce(r, n, V);
                    }
                  }
                  break;
                case 5:
                  Mn(C, C.return);
                  break;
                case 22:
                  if (C.memoizedState !== null) {
                    ga(N);
                    continue;
                  }
              }
              I !== null ? (I.return = C, A = I) : ga(N);
            }
            j = j.sibling;
          }
          e: for (j = null, N = e; ; ) {
            if (N.tag === 5) {
              if (j === null) {
                j = N;
                try {
                  l = N.stateNode, y ? (i = l.style, typeof i.setProperty == "function" ? i.setProperty("display", "none", "important") : i.display = "none") : (u = N.stateNode, d = N.memoizedProps.style, o = d != null && d.hasOwnProperty("display") ? d.display : null, u.style.display = qo("display", o));
                } catch (V) {
                  Ce(e, e.return, V);
                }
              }
            } else if (N.tag === 6) {
              if (j === null) try {
                N.stateNode.nodeValue = y ? "" : N.memoizedProps;
              } catch (V) {
                Ce(e, e.return, V);
              }
            } else if ((N.tag !== 22 && N.tag !== 23 || N.memoizedState === null || N === e) && N.child !== null) {
              N.child.return = N, N = N.child;
              continue;
            }
            if (N === e) break e;
            for (; N.sibling === null; ) {
              if (N.return === null || N.return === e) break e;
              j === N && (j = null), N = N.return;
            }
            j === N && (j = null), N.sibling.return = N.return, N = N.sibling;
          }
        }
        break;
      case 19:
        vt(t, e), St(e), r & 4 && pa(e);
        break;
      case 21:
        break;
      default:
        vt(
          t,
          e
        ), St(e);
    }
  }
  function St(e) {
    var t = e.flags;
    if (t & 2) {
      try {
        e: {
          for (var n = e.return; n !== null; ) {
            if (ca(n)) {
              var r = n;
              break e;
            }
            n = n.return;
          }
          throw Error(a(160));
        }
        switch (r.tag) {
          case 5:
            var l = r.stateNode;
            r.flags & 32 && (An(l, ""), r.flags &= -33);
            var i = da(e);
            co(e, i, l);
            break;
          case 3:
          case 4:
            var o = r.stateNode.containerInfo, u = da(e);
            ao(e, u, o);
            break;
          default:
            throw Error(a(161));
        }
      } catch (d) {
        Ce(e, e.return, d);
      }
      e.flags &= -3;
    }
    t & 4096 && (e.flags &= -4097);
  }
  function Ud(e, t, n) {
    A = e, ha(e);
  }
  function ha(e, t, n) {
    for (var r = (e.mode & 1) !== 0; A !== null; ) {
      var l = A, i = l.child;
      if (l.tag === 22 && r) {
        var o = l.memoizedState !== null || hl;
        if (!o) {
          var u = l.alternate, d = u !== null && u.memoizedState !== null || Be;
          u = hl;
          var y = Be;
          if (hl = o, (Be = d) && !y) for (A = l; A !== null; ) o = A, d = o.child, o.tag === 22 && o.memoizedState !== null ? ya(l) : d !== null ? (d.return = o, A = d) : ya(l);
          for (; i !== null; ) A = i, ha(i), i = i.sibling;
          A = l, hl = u, Be = y;
        }
        va(e);
      } else (l.subtreeFlags & 8772) !== 0 && i !== null ? (i.return = l, A = i) : va(e);
    }
  }
  function va(e) {
    for (; A !== null; ) {
      var t = A;
      if ((t.flags & 8772) !== 0) {
        var n = t.alternate;
        try {
          if ((t.flags & 8772) !== 0) switch (t.tag) {
            case 0:
            case 11:
            case 15:
              Be || vl(5, t);
              break;
            case 1:
              var r = t.stateNode;
              if (t.flags & 4 && !Be) if (n === null) r.componentDidMount();
              else {
                var l = t.elementType === t.type ? n.memoizedProps : mt(t.type, n.memoizedProps);
                r.componentDidUpdate(l, n.memoizedState, r.__reactInternalSnapshotBeforeUpdate);
              }
              var i = t.updateQueue;
              i !== null && gu(t, i, r);
              break;
            case 3:
              var o = t.updateQueue;
              if (o !== null) {
                if (n = null, t.child !== null) switch (t.child.tag) {
                  case 5:
                    n = t.child.stateNode;
                    break;
                  case 1:
                    n = t.child.stateNode;
                }
                gu(t, o, n);
              }
              break;
            case 5:
              var u = t.stateNode;
              if (n === null && t.flags & 4) {
                n = u;
                var d = t.memoizedProps;
                switch (t.type) {
                  case "button":
                  case "input":
                  case "select":
                  case "textarea":
                    d.autoFocus && n.focus();
                    break;
                  case "img":
                    d.src && (n.src = d.src);
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
              if (t.memoizedState === null) {
                var y = t.alternate;
                if (y !== null) {
                  var j = y.memoizedState;
                  if (j !== null) {
                    var N = j.dehydrated;
                    N !== null && Yn(N);
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
              throw Error(a(163));
          }
          Be || t.flags & 512 && uo(t);
        } catch (C) {
          Ce(t, t.return, C);
        }
      }
      if (t === e) {
        A = null;
        break;
      }
      if (n = t.sibling, n !== null) {
        n.return = t.return, A = n;
        break;
      }
      A = t.return;
    }
  }
  function ga(e) {
    for (; A !== null; ) {
      var t = A;
      if (t === e) {
        A = null;
        break;
      }
      var n = t.sibling;
      if (n !== null) {
        n.return = t.return, A = n;
        break;
      }
      A = t.return;
    }
  }
  function ya(e) {
    for (; A !== null; ) {
      var t = A;
      try {
        switch (t.tag) {
          case 0:
          case 11:
          case 15:
            var n = t.return;
            try {
              vl(4, t);
            } catch (d) {
              Ce(t, n, d);
            }
            break;
          case 1:
            var r = t.stateNode;
            if (typeof r.componentDidMount == "function") {
              var l = t.return;
              try {
                r.componentDidMount();
              } catch (d) {
                Ce(t, l, d);
              }
            }
            var i = t.return;
            try {
              uo(t);
            } catch (d) {
              Ce(t, i, d);
            }
            break;
          case 5:
            var o = t.return;
            try {
              uo(t);
            } catch (d) {
              Ce(t, o, d);
            }
        }
      } catch (d) {
        Ce(t, t.return, d);
      }
      if (t === e) {
        A = null;
        break;
      }
      var u = t.sibling;
      if (u !== null) {
        u.return = t.return, A = u;
        break;
      }
      A = t.return;
    }
  }
  var Vd = Math.ceil, gl = X.ReactCurrentDispatcher, fo = X.ReactCurrentOwner, at = X.ReactCurrentBatchConfig, le = 0, Ie = null, Pe = null, Ue = 0, lt = 0, zn = Ft(0), Me = 0, gr = null, ln = 0, yl = 0, po = 0, yr = null, Ze = null, mo = 0, In = 1 / 0, Lt = null, wl = !1, ho = null, Wt = null, xl = !1, Qt = null, kl = 0, wr = 0, vo = null, Sl = -1, _l = 0;
  function Ge() {
    return (le & 6) !== 0 ? Ee() : Sl !== -1 ? Sl : Sl = Ee();
  }
  function Gt(e) {
    return (e.mode & 1) === 0 ? 1 : (le & 2) !== 0 && Ue !== 0 ? Ue & -Ue : _d.transition !== null ? (_l === 0 && (_l = ds()), _l) : (e = de, e !== 0 || (e = window.event, e = e === void 0 ? 16 : xs(e.type)), e);
  }
  function gt(e, t, n, r) {
    if (50 < wr) throw wr = 0, vo = null, Error(a(185));
    Bn(e, n, r), ((le & 2) === 0 || e !== Ie) && (e === Ie && ((le & 2) === 0 && (yl |= n), Me === 4 && Kt(e, Ue)), Je(e, r), n === 1 && le === 0 && (t.mode & 1) === 0 && (In = Ee() + 500, Zr && Vt()));
  }
  function Je(e, t) {
    var n = e.callbackNode;
    _c(e, t);
    var r = Mr(e, e === Ie ? Ue : 0);
    if (r === 0) n !== null && us(n), e.callbackNode = null, e.callbackPriority = 0;
    else if (t = r & -r, e.callbackPriority !== t) {
      if (n != null && us(n), t === 1) e.tag === 0 ? Sd(xa.bind(null, e)) : iu(xa.bind(null, e)), yd(function() {
        (le & 6) === 0 && Vt();
      }), n = null;
      else {
        switch (fs(r)) {
          case 1:
            n = Yl;
            break;
          case 4:
            n = as;
            break;
          case 16:
            n = Pr;
            break;
          case 536870912:
            n = cs;
            break;
          default:
            n = Pr;
        }
        n = Pa(n, wa.bind(null, e));
      }
      e.callbackPriority = t, e.callbackNode = n;
    }
  }
  function wa(e, t) {
    if (Sl = -1, _l = 0, (le & 6) !== 0) throw Error(a(327));
    var n = e.callbackNode;
    if (Dn() && e.callbackNode !== n) return null;
    var r = Mr(e, e === Ie ? Ue : 0);
    if (r === 0) return null;
    if ((r & 30) !== 0 || (r & e.expiredLanes) !== 0 || t) t = Cl(e, r);
    else {
      t = r;
      var l = le;
      le |= 2;
      var i = Sa();
      (Ie !== e || Ue !== t) && (Lt = null, In = Ee() + 500, sn(e, t));
      do
        try {
          Bd();
          break;
        } catch (u) {
          ka(e, u);
        }
      while (!0);
      zi(), gl.current = i, le = l, Pe !== null ? t = 0 : (Ie = null, Ue = 0, t = Me);
    }
    if (t !== 0) {
      if (t === 2 && (l = ql(e), l !== 0 && (r = l, t = go(e, l))), t === 1) throw n = gr, sn(e, 0), Kt(e, r), Je(e, Ee()), n;
      if (t === 6) Kt(e, r);
      else {
        if (l = e.current.alternate, (r & 30) === 0 && !$d(l) && (t = Cl(e, r), t === 2 && (i = ql(e), i !== 0 && (r = i, t = go(e, i))), t === 1)) throw n = gr, sn(e, 0), Kt(e, r), Je(e, Ee()), n;
        switch (e.finishedWork = l, e.finishedLanes = r, t) {
          case 0:
          case 1:
            throw Error(a(345));
          case 2:
            un(e, Ze, Lt);
            break;
          case 3:
            if (Kt(e, r), (r & 130023424) === r && (t = mo + 500 - Ee(), 10 < t)) {
              if (Mr(e, 0) !== 0) break;
              if (l = e.suspendedLanes, (l & r) !== r) {
                Ge(), e.pingedLanes |= e.suspendedLanes & l;
                break;
              }
              e.timeoutHandle = Si(un.bind(null, e, Ze, Lt), t);
              break;
            }
            un(e, Ze, Lt);
            break;
          case 4:
            if (Kt(e, r), (r & 4194240) === r) break;
            for (t = e.eventTimes, l = -1; 0 < r; ) {
              var o = 31 - dt(r);
              i = 1 << o, o = t[o], o > l && (l = o), r &= ~i;
            }
            if (r = l, r = Ee() - r, r = (120 > r ? 120 : 480 > r ? 480 : 1080 > r ? 1080 : 1920 > r ? 1920 : 3e3 > r ? 3e3 : 4320 > r ? 4320 : 1960 * Vd(r / 1960)) - r, 10 < r) {
              e.timeoutHandle = Si(un.bind(null, e, Ze, Lt), r);
              break;
            }
            un(e, Ze, Lt);
            break;
          case 5:
            un(e, Ze, Lt);
            break;
          default:
            throw Error(a(329));
        }
      }
    }
    return Je(e, Ee()), e.callbackNode === n ? wa.bind(null, e) : null;
  }
  function go(e, t) {
    var n = yr;
    return e.current.memoizedState.isDehydrated && (sn(e, t).flags |= 256), e = Cl(e, t), e !== 2 && (t = Ze, Ze = n, t !== null && yo(t)), e;
  }
  function yo(e) {
    Ze === null ? Ze = e : Ze.push.apply(Ze, e);
  }
  function $d(e) {
    for (var t = e; ; ) {
      if (t.flags & 16384) {
        var n = t.updateQueue;
        if (n !== null && (n = n.stores, n !== null)) for (var r = 0; r < n.length; r++) {
          var l = n[r], i = l.getSnapshot;
          l = l.value;
          try {
            if (!ft(i(), l)) return !1;
          } catch {
            return !1;
          }
        }
      }
      if (n = t.child, t.subtreeFlags & 16384 && n !== null) n.return = t, t = n;
      else {
        if (t === e) break;
        for (; t.sibling === null; ) {
          if (t.return === null || t.return === e) return !0;
          t = t.return;
        }
        t.sibling.return = t.return, t = t.sibling;
      }
    }
    return !0;
  }
  function Kt(e, t) {
    for (t &= ~po, t &= ~yl, e.suspendedLanes |= t, e.pingedLanes &= ~t, e = e.expirationTimes; 0 < t; ) {
      var n = 31 - dt(t), r = 1 << n;
      e[n] = -1, t &= ~r;
    }
  }
  function xa(e) {
    if ((le & 6) !== 0) throw Error(a(327));
    Dn();
    var t = Mr(e, 0);
    if ((t & 1) === 0) return Je(e, Ee()), null;
    var n = Cl(e, t);
    if (e.tag !== 0 && n === 2) {
      var r = ql(e);
      r !== 0 && (t = r, n = go(e, r));
    }
    if (n === 1) throw n = gr, sn(e, 0), Kt(e, t), Je(e, Ee()), n;
    if (n === 6) throw Error(a(345));
    return e.finishedWork = e.current.alternate, e.finishedLanes = t, un(e, Ze, Lt), Je(e, Ee()), null;
  }
  function wo(e, t) {
    var n = le;
    le |= 1;
    try {
      return e(t);
    } finally {
      le = n, le === 0 && (In = Ee() + 500, Zr && Vt());
    }
  }
  function on(e) {
    Qt !== null && Qt.tag === 0 && (le & 6) === 0 && Dn();
    var t = le;
    le |= 1;
    var n = at.transition, r = de;
    try {
      if (at.transition = null, de = 1, e) return e();
    } finally {
      de = r, at.transition = n, le = t, (le & 6) === 0 && Vt();
    }
  }
  function xo() {
    lt = zn.current, ve(zn);
  }
  function sn(e, t) {
    e.finishedWork = null, e.finishedLanes = 0;
    var n = e.timeoutHandle;
    if (n !== -1 && (e.timeoutHandle = -1, gd(n)), Pe !== null) for (n = Pe.return; n !== null; ) {
      var r = n;
      switch (Pi(r), r.tag) {
        case 1:
          r = r.type.childContextTypes, r != null && qr();
          break;
        case 3:
          Ln(), ve(Ye), ve(Ve), $i();
          break;
        case 5:
          Ui(r);
          break;
        case 4:
          Ln();
          break;
        case 13:
          ve(ke);
          break;
        case 19:
          ve(ke);
          break;
        case 10:
          Ii(r.type._context);
          break;
        case 22:
        case 23:
          xo();
      }
      n = n.return;
    }
    if (Ie = e, Pe = e = Yt(e.current, null), Ue = lt = t, Me = 0, gr = null, po = yl = ln = 0, Ze = yr = null, tn !== null) {
      for (t = 0; t < tn.length; t++) if (n = tn[t], r = n.interleaved, r !== null) {
        n.interleaved = null;
        var l = r.next, i = n.pending;
        if (i !== null) {
          var o = i.next;
          i.next = l, r.next = o;
        }
        n.pending = r;
      }
      tn = null;
    }
    return e;
  }
  function ka(e, t) {
    do {
      var n = Pe;
      try {
        if (zi(), sl.current = dl, ul) {
          for (var r = Se.memoizedState; r !== null; ) {
            var l = r.queue;
            l !== null && (l.pending = null), r = r.next;
          }
          ul = !1;
        }
        if (rn = 0, ze = Re = Se = null, dr = !1, fr = 0, fo.current = null, n === null || n.return === null) {
          Me = 1, gr = t, Pe = null;
          break;
        }
        e: {
          var i = e, o = n.return, u = n, d = t;
          if (t = Ue, u.flags |= 32768, d !== null && typeof d == "object" && typeof d.then == "function") {
            var y = d, j = u, N = j.tag;
            if ((j.mode & 1) === 0 && (N === 0 || N === 11 || N === 15)) {
              var C = j.alternate;
              C ? (j.updateQueue = C.updateQueue, j.memoizedState = C.memoizedState, j.lanes = C.lanes) : (j.updateQueue = null, j.memoizedState = null);
            }
            var I = Gu(o);
            if (I !== null) {
              I.flags &= -257, Ku(I, o, u, i, t), I.mode & 1 && Qu(i, y, t), t = I, d = y;
              var U = t.updateQueue;
              if (U === null) {
                var V = /* @__PURE__ */ new Set();
                V.add(d), t.updateQueue = V;
              } else U.add(d);
              break e;
            } else {
              if ((t & 1) === 0) {
                Qu(i, y, t), ko();
                break e;
              }
              d = Error(a(426));
            }
          } else if (we && u.mode & 1) {
            var Ne = Gu(o);
            if (Ne !== null) {
              (Ne.flags & 65536) === 0 && (Ne.flags |= 256), Ku(Ne, o, u, i, t), Ri(Rn(d, u));
              break e;
            }
          }
          i = d = Rn(d, u), Me !== 4 && (Me = 2), yr === null ? yr = [i] : yr.push(i), i = o;
          do {
            switch (i.tag) {
              case 3:
                i.flags |= 65536, t &= -t, i.lanes |= t;
                var h = Bu(i, d, t);
                vu(i, h);
                break e;
              case 1:
                u = d;
                var p = i.type, v = i.stateNode;
                if ((i.flags & 128) === 0 && (typeof p.getDerivedStateFromError == "function" || v !== null && typeof v.componentDidCatch == "function" && (Wt === null || !Wt.has(v)))) {
                  i.flags |= 65536, t &= -t, i.lanes |= t;
                  var T = Wu(i, u, t);
                  vu(i, T);
                  break e;
                }
            }
            i = i.return;
          } while (i !== null);
        }
        Ca(n);
      } catch (H) {
        t = H, Pe === n && n !== null && (Pe = n = n.return);
        continue;
      }
      break;
    } while (!0);
  }
  function Sa() {
    var e = gl.current;
    return gl.current = dl, e === null ? dl : e;
  }
  function ko() {
    (Me === 0 || Me === 3 || Me === 2) && (Me = 4), Ie === null || (ln & 268435455) === 0 && (yl & 268435455) === 0 || Kt(Ie, Ue);
  }
  function Cl(e, t) {
    var n = le;
    le |= 2;
    var r = Sa();
    (Ie !== e || Ue !== t) && (Lt = null, sn(e, t));
    do
      try {
        Hd();
        break;
      } catch (l) {
        ka(e, l);
      }
    while (!0);
    if (zi(), le = n, gl.current = r, Pe !== null) throw Error(a(261));
    return Ie = null, Ue = 0, Me;
  }
  function Hd() {
    for (; Pe !== null; ) _a(Pe);
  }
  function Bd() {
    for (; Pe !== null && !mc(); ) _a(Pe);
  }
  function _a(e) {
    var t = Na(e.alternate, e, lt);
    e.memoizedProps = e.pendingProps, t === null ? Ca(e) : Pe = t, fo.current = null;
  }
  function Ca(e) {
    var t = e;
    do {
      var n = t.alternate;
      if (e = t.return, (t.flags & 32768) === 0) {
        if (n = Dd(n, t, lt), n !== null) {
          Pe = n;
          return;
        }
      } else {
        if (n = Od(n, t), n !== null) {
          n.flags &= 32767, Pe = n;
          return;
        }
        if (e !== null) e.flags |= 32768, e.subtreeFlags = 0, e.deletions = null;
        else {
          Me = 6, Pe = null;
          return;
        }
      }
      if (t = t.sibling, t !== null) {
        Pe = t;
        return;
      }
      Pe = t = e;
    } while (t !== null);
    Me === 0 && (Me = 5);
  }
  function un(e, t, n) {
    var r = de, l = at.transition;
    try {
      at.transition = null, de = 1, Wd(e, t, n, r);
    } finally {
      at.transition = l, de = r;
    }
    return null;
  }
  function Wd(e, t, n, r) {
    do
      Dn();
    while (Qt !== null);
    if ((le & 6) !== 0) throw Error(a(327));
    n = e.finishedWork;
    var l = e.finishedLanes;
    if (n === null) return null;
    if (e.finishedWork = null, e.finishedLanes = 0, n === e.current) throw Error(a(177));
    e.callbackNode = null, e.callbackPriority = 0;
    var i = n.lanes | n.childLanes;
    if (Cc(e, i), e === Ie && (Pe = Ie = null, Ue = 0), (n.subtreeFlags & 2064) === 0 && (n.flags & 2064) === 0 || xl || (xl = !0, Pa(Pr, function() {
      return Dn(), null;
    })), i = (n.flags & 15990) !== 0, (n.subtreeFlags & 15990) !== 0 || i) {
      i = at.transition, at.transition = null;
      var o = de;
      de = 1;
      var u = le;
      le |= 4, fo.current = null, Fd(e, n), ma(n, e), cd(xi), Dr = !!wi, xi = wi = null, e.current = n, Ud(n), hc(), le = u, de = o, at.transition = i;
    } else e.current = n;
    if (xl && (xl = !1, Qt = e, kl = l), i = e.pendingLanes, i === 0 && (Wt = null), yc(n.stateNode), Je(e, Ee()), t !== null) for (r = e.onRecoverableError, n = 0; n < t.length; n++) l = t[n], r(l.value, { componentStack: l.stack, digest: l.digest });
    if (wl) throw wl = !1, e = ho, ho = null, e;
    return (kl & 1) !== 0 && e.tag !== 0 && Dn(), i = e.pendingLanes, (i & 1) !== 0 ? e === vo ? wr++ : (wr = 0, vo = e) : wr = 0, Vt(), null;
  }
  function Dn() {
    if (Qt !== null) {
      var e = fs(kl), t = at.transition, n = de;
      try {
        if (at.transition = null, de = 16 > e ? 16 : e, Qt === null) var r = !1;
        else {
          if (e = Qt, Qt = null, kl = 0, (le & 6) !== 0) throw Error(a(331));
          var l = le;
          for (le |= 4, A = e.current; A !== null; ) {
            var i = A, o = i.child;
            if ((A.flags & 16) !== 0) {
              var u = i.deletions;
              if (u !== null) {
                for (var d = 0; d < u.length; d++) {
                  var y = u[d];
                  for (A = y; A !== null; ) {
                    var j = A;
                    switch (j.tag) {
                      case 0:
                      case 11:
                      case 15:
                        vr(8, j, i);
                    }
                    var N = j.child;
                    if (N !== null) N.return = j, A = N;
                    else for (; A !== null; ) {
                      j = A;
                      var C = j.sibling, I = j.return;
                      if (aa(j), j === y) {
                        A = null;
                        break;
                      }
                      if (C !== null) {
                        C.return = I, A = C;
                        break;
                      }
                      A = I;
                    }
                  }
                }
                var U = i.alternate;
                if (U !== null) {
                  var V = U.child;
                  if (V !== null) {
                    U.child = null;
                    do {
                      var Ne = V.sibling;
                      V.sibling = null, V = Ne;
                    } while (V !== null);
                  }
                }
                A = i;
              }
            }
            if ((i.subtreeFlags & 2064) !== 0 && o !== null) o.return = i, A = o;
            else e: for (; A !== null; ) {
              if (i = A, (i.flags & 2048) !== 0) switch (i.tag) {
                case 0:
                case 11:
                case 15:
                  vr(9, i, i.return);
              }
              var h = i.sibling;
              if (h !== null) {
                h.return = i.return, A = h;
                break e;
              }
              A = i.return;
            }
          }
          var p = e.current;
          for (A = p; A !== null; ) {
            o = A;
            var v = o.child;
            if ((o.subtreeFlags & 2064) !== 0 && v !== null) v.return = o, A = v;
            else e: for (o = p; A !== null; ) {
              if (u = A, (u.flags & 2048) !== 0) try {
                switch (u.tag) {
                  case 0:
                  case 11:
                  case 15:
                    vl(9, u);
                }
              } catch (H) {
                Ce(u, u.return, H);
              }
              if (u === o) {
                A = null;
                break e;
              }
              var T = u.sibling;
              if (T !== null) {
                T.return = u.return, A = T;
                break e;
              }
              A = u.return;
            }
          }
          if (le = l, Vt(), yt && typeof yt.onPostCommitFiberRoot == "function") try {
            yt.onPostCommitFiberRoot(Tr, e);
          } catch {
          }
          r = !0;
        }
        return r;
      } finally {
        de = n, at.transition = t;
      }
    }
    return !1;
  }
  function ja(e, t, n) {
    t = Rn(n, t), t = Bu(e, t, 1), e = Ht(e, t, 1), t = Ge(), e !== null && (Bn(e, 1, t), Je(e, t));
  }
  function Ce(e, t, n) {
    if (e.tag === 3) ja(e, e, n);
    else for (; t !== null; ) {
      if (t.tag === 3) {
        ja(t, e, n);
        break;
      } else if (t.tag === 1) {
        var r = t.stateNode;
        if (typeof t.type.getDerivedStateFromError == "function" || typeof r.componentDidCatch == "function" && (Wt === null || !Wt.has(r))) {
          e = Rn(n, e), e = Wu(t, e, 1), t = Ht(t, e, 1), e = Ge(), t !== null && (Bn(t, 1, e), Je(t, e));
          break;
        }
      }
      t = t.return;
    }
  }
  function Qd(e, t, n) {
    var r = e.pingCache;
    r !== null && r.delete(t), t = Ge(), e.pingedLanes |= e.suspendedLanes & n, Ie === e && (Ue & n) === n && (Me === 4 || Me === 3 && (Ue & 130023424) === Ue && 500 > Ee() - mo ? sn(e, 0) : po |= n), Je(e, t);
  }
  function Ea(e, t) {
    t === 0 && ((e.mode & 1) === 0 ? t = 1 : (t = Rr, Rr <<= 1, (Rr & 130023424) === 0 && (Rr = 4194304)));
    var n = Ge();
    e = Nt(e, t), e !== null && (Bn(e, t, n), Je(e, n));
  }
  function Gd(e) {
    var t = e.memoizedState, n = 0;
    t !== null && (n = t.retryLane), Ea(e, n);
  }
  function Kd(e, t) {
    var n = 0;
    switch (e.tag) {
      case 13:
        var r = e.stateNode, l = e.memoizedState;
        l !== null && (n = l.retryLane);
        break;
      case 19:
        r = e.stateNode;
        break;
      default:
        throw Error(a(314));
    }
    r !== null && r.delete(t), Ea(e, n);
  }
  var Na;
  Na = function(e, t, n) {
    if (e !== null) if (e.memoizedProps !== t.pendingProps || Ye.current) Xe = !0;
    else {
      if ((e.lanes & n) === 0 && (t.flags & 128) === 0) return Xe = !1, Id(e, t, n);
      Xe = (e.flags & 131072) !== 0;
    }
    else Xe = !1, we && (t.flags & 1048576) !== 0 && ou(t, br, t.index);
    switch (t.lanes = 0, t.tag) {
      case 2:
        var r = t.type;
        ml(e, t), e = t.pendingProps;
        var l = _n(t, Ve.current);
        Tn(t, n), l = Wi(null, t, r, e, l, n);
        var i = Qi();
        return t.flags |= 1, typeof l == "object" && l !== null && typeof l.render == "function" && l.$$typeof === void 0 ? (t.tag = 1, t.memoizedState = null, t.updateQueue = null, qe(r) ? (i = !0, Xr(t)) : i = !1, t.memoizedState = l.state !== null && l.state !== void 0 ? l.state : null, Ai(t), l.updater = fl, t.stateNode = l, l._reactInternals = t, Zi(t, r, e, n), t = to(null, t, r, !0, i, n)) : (t.tag = 0, we && i && Ni(t), Qe(null, t, l, n), t = t.child), t;
      case 16:
        r = t.elementType;
        e: {
          switch (ml(e, t), e = t.pendingProps, l = r._init, r = l(r._payload), t.type = r, l = t.tag = qd(r), e = mt(r, e), l) {
            case 0:
              t = eo(null, t, r, e, n);
              break e;
            case 1:
              t = bu(null, t, r, e, n);
              break e;
            case 11:
              t = Yu(null, t, r, e, n);
              break e;
            case 14:
              t = qu(null, t, r, mt(r.type, e), n);
              break e;
          }
          throw Error(a(
            306,
            r,
            ""
          ));
        }
        return t;
      case 0:
        return r = t.type, l = t.pendingProps, l = t.elementType === r ? l : mt(r, l), eo(e, t, r, l, n);
      case 1:
        return r = t.type, l = t.pendingProps, l = t.elementType === r ? l : mt(r, l), bu(e, t, r, l, n);
      case 3:
        e: {
          if (ea(t), e === null) throw Error(a(387));
          r = t.pendingProps, i = t.memoizedState, l = i.element, hu(e, t), il(t, r, null, n);
          var o = t.memoizedState;
          if (r = o.element, i.isDehydrated) if (i = { element: r, isDehydrated: !1, cache: o.cache, pendingSuspenseBoundaries: o.pendingSuspenseBoundaries, transitions: o.transitions }, t.updateQueue.baseState = i, t.memoizedState = i, t.flags & 256) {
            l = Rn(Error(a(423)), t), t = ta(e, t, r, n, l);
            break e;
          } else if (r !== l) {
            l = Rn(Error(a(424)), t), t = ta(e, t, r, n, l);
            break e;
          } else for (rt = At(t.stateNode.containerInfo.firstChild), nt = t, we = !0, pt = null, n = pu(t, null, r, n), t.child = n; n; ) n.flags = n.flags & -3 | 4096, n = n.sibling;
          else {
            if (En(), r === l) {
              t = Tt(e, t, n);
              break e;
            }
            Qe(e, t, r, n);
          }
          t = t.child;
        }
        return t;
      case 5:
        return yu(t), e === null && Li(t), r = t.type, l = t.pendingProps, i = e !== null ? e.memoizedProps : null, o = l.children, ki(r, l) ? o = null : i !== null && ki(r, i) && (t.flags |= 32), Ju(e, t), Qe(e, t, o, n), t.child;
      case 6:
        return e === null && Li(t), null;
      case 13:
        return na(e, t, n);
      case 4:
        return Fi(t, t.stateNode.containerInfo), r = t.pendingProps, e === null ? t.child = Nn(t, null, r, n) : Qe(e, t, r, n), t.child;
      case 11:
        return r = t.type, l = t.pendingProps, l = t.elementType === r ? l : mt(r, l), Yu(e, t, r, l, n);
      case 7:
        return Qe(e, t, t.pendingProps, n), t.child;
      case 8:
        return Qe(e, t, t.pendingProps.children, n), t.child;
      case 12:
        return Qe(e, t, t.pendingProps.children, n), t.child;
      case 10:
        e: {
          if (r = t.type._context, l = t.pendingProps, i = t.memoizedProps, o = l.value, me(nl, r._currentValue), r._currentValue = o, i !== null) if (ft(i.value, o)) {
            if (i.children === l.children && !Ye.current) {
              t = Tt(e, t, n);
              break e;
            }
          } else for (i = t.child, i !== null && (i.return = t); i !== null; ) {
            var u = i.dependencies;
            if (u !== null) {
              o = i.child;
              for (var d = u.firstContext; d !== null; ) {
                if (d.context === r) {
                  if (i.tag === 1) {
                    d = Pt(-1, n & -n), d.tag = 2;
                    var y = i.updateQueue;
                    if (y !== null) {
                      y = y.shared;
                      var j = y.pending;
                      j === null ? d.next = d : (d.next = j.next, j.next = d), y.pending = d;
                    }
                  }
                  i.lanes |= n, d = i.alternate, d !== null && (d.lanes |= n), Di(
                    i.return,
                    n,
                    t
                  ), u.lanes |= n;
                  break;
                }
                d = d.next;
              }
            } else if (i.tag === 10) o = i.type === t.type ? null : i.child;
            else if (i.tag === 18) {
              if (o = i.return, o === null) throw Error(a(341));
              o.lanes |= n, u = o.alternate, u !== null && (u.lanes |= n), Di(o, n, t), o = i.sibling;
            } else o = i.child;
            if (o !== null) o.return = i;
            else for (o = i; o !== null; ) {
              if (o === t) {
                o = null;
                break;
              }
              if (i = o.sibling, i !== null) {
                i.return = o.return, o = i;
                break;
              }
              o = o.return;
            }
            i = o;
          }
          Qe(e, t, l.children, n), t = t.child;
        }
        return t;
      case 9:
        return l = t.type, r = t.pendingProps.children, Tn(t, n), l = st(l), r = r(l), t.flags |= 1, Qe(e, t, r, n), t.child;
      case 14:
        return r = t.type, l = mt(r, t.pendingProps), l = mt(r.type, l), qu(e, t, r, l, n);
      case 15:
        return Xu(e, t, t.type, t.pendingProps, n);
      case 17:
        return r = t.type, l = t.pendingProps, l = t.elementType === r ? l : mt(r, l), ml(e, t), t.tag = 1, qe(r) ? (e = !0, Xr(t)) : e = !1, Tn(t, n), $u(t, r, l), Zi(t, r, l, n), to(null, t, r, !0, e, n);
      case 19:
        return la(e, t, n);
      case 22:
        return Zu(e, t, n);
    }
    throw Error(a(156, t.tag));
  };
  function Pa(e, t) {
    return ss(e, t);
  }
  function Yd(e, t, n, r) {
    this.tag = e, this.key = n, this.sibling = this.child = this.return = this.stateNode = this.type = this.elementType = null, this.index = 0, this.ref = null, this.pendingProps = t, this.dependencies = this.memoizedState = this.updateQueue = this.memoizedProps = null, this.mode = r, this.subtreeFlags = this.flags = 0, this.deletions = null, this.childLanes = this.lanes = 0, this.alternate = null;
  }
  function ct(e, t, n, r) {
    return new Yd(e, t, n, r);
  }
  function So(e) {
    return e = e.prototype, !(!e || !e.isReactComponent);
  }
  function qd(e) {
    if (typeof e == "function") return So(e) ? 1 : 0;
    if (e != null) {
      if (e = e.$$typeof, e === Te) return 11;
      if (e === We) return 14;
    }
    return 2;
  }
  function Yt(e, t) {
    var n = e.alternate;
    return n === null ? (n = ct(e.tag, t, e.key, e.mode), n.elementType = e.elementType, n.type = e.type, n.stateNode = e.stateNode, n.alternate = e, e.alternate = n) : (n.pendingProps = t, n.type = e.type, n.flags = 0, n.subtreeFlags = 0, n.deletions = null), n.flags = e.flags & 14680064, n.childLanes = e.childLanes, n.lanes = e.lanes, n.child = e.child, n.memoizedProps = e.memoizedProps, n.memoizedState = e.memoizedState, n.updateQueue = e.updateQueue, t = e.dependencies, n.dependencies = t === null ? null : { lanes: t.lanes, firstContext: t.firstContext }, n.sibling = e.sibling, n.index = e.index, n.ref = e.ref, n;
  }
  function jl(e, t, n, r, l, i) {
    var o = 2;
    if (r = e, typeof e == "function") So(e) && (o = 1);
    else if (typeof e == "string") o = 5;
    else e: switch (e) {
      case Z:
        return an(n.children, l, i, t);
      case ee:
        o = 8, l |= 8;
        break;
      case xe:
        return e = ct(12, n, t, l | 2), e.elementType = xe, e.lanes = i, e;
      case je:
        return e = ct(13, n, t, l), e.elementType = je, e.lanes = i, e;
      case Ae:
        return e = ct(19, n, t, l), e.elementType = Ae, e.lanes = i, e;
      case ue:
        return El(n, l, i, t);
      default:
        if (typeof e == "object" && e !== null) switch (e.$$typeof) {
          case fe:
            o = 10;
            break e;
          case Oe:
            o = 9;
            break e;
          case Te:
            o = 11;
            break e;
          case We:
            o = 14;
            break e;
          case Le:
            o = 16, r = null;
            break e;
        }
        throw Error(a(130, e == null ? e : typeof e, ""));
    }
    return t = ct(o, n, t, l), t.elementType = e, t.type = r, t.lanes = i, t;
  }
  function an(e, t, n, r) {
    return e = ct(7, e, r, t), e.lanes = n, e;
  }
  function El(e, t, n, r) {
    return e = ct(22, e, r, t), e.elementType = ue, e.lanes = n, e.stateNode = { isHidden: !1 }, e;
  }
  function _o(e, t, n) {
    return e = ct(6, e, null, t), e.lanes = n, e;
  }
  function Co(e, t, n) {
    return t = ct(4, e.children !== null ? e.children : [], e.key, t), t.lanes = n, t.stateNode = { containerInfo: e.containerInfo, pendingChildren: null, implementation: e.implementation }, t;
  }
  function Xd(e, t, n, r, l) {
    this.tag = t, this.containerInfo = e, this.finishedWork = this.pingCache = this.current = this.pendingChildren = null, this.timeoutHandle = -1, this.callbackNode = this.pendingContext = this.context = null, this.callbackPriority = 0, this.eventTimes = Xl(0), this.expirationTimes = Xl(-1), this.entangledLanes = this.finishedLanes = this.mutableReadLanes = this.expiredLanes = this.pingedLanes = this.suspendedLanes = this.pendingLanes = 0, this.entanglements = Xl(0), this.identifierPrefix = r, this.onRecoverableError = l, this.mutableSourceEagerHydrationData = null;
  }
  function jo(e, t, n, r, l, i, o, u, d) {
    return e = new Xd(e, t, n, u, d), t === 1 ? (t = 1, i === !0 && (t |= 8)) : t = 0, i = ct(3, null, null, t), e.current = i, i.stateNode = e, i.memoizedState = { element: r, isDehydrated: n, cache: null, transitions: null, pendingSuspenseBoundaries: null }, Ai(i), e;
  }
  function Zd(e, t, n) {
    var r = 3 < arguments.length && arguments[3] !== void 0 ? arguments[3] : null;
    return { $$typeof: b, key: r == null ? null : "" + r, children: e, containerInfo: t, implementation: n };
  }
  function Ta(e) {
    if (!e) return Ut;
    e = e._reactInternals;
    e: {
      if (Xt(e) !== e || e.tag !== 1) throw Error(a(170));
      var t = e;
      do {
        switch (t.tag) {
          case 3:
            t = t.stateNode.context;
            break e;
          case 1:
            if (qe(t.type)) {
              t = t.stateNode.__reactInternalMemoizedMergedChildContext;
              break e;
            }
        }
        t = t.return;
      } while (t !== null);
      throw Error(a(171));
    }
    if (e.tag === 1) {
      var n = e.type;
      if (qe(n)) return ru(e, n, t);
    }
    return t;
  }
  function La(e, t, n, r, l, i, o, u, d) {
    return e = jo(n, r, !0, e, l, i, o, u, d), e.context = Ta(null), n = e.current, r = Ge(), l = Gt(n), i = Pt(r, l), i.callback = t ?? null, Ht(n, i, l), e.current.lanes = l, Bn(e, l, r), Je(e, r), e;
  }
  function Nl(e, t, n, r) {
    var l = t.current, i = Ge(), o = Gt(l);
    return n = Ta(n), t.context === null ? t.context = n : t.pendingContext = n, t = Pt(i, o), t.payload = { element: e }, r = r === void 0 ? null : r, r !== null && (t.callback = r), e = Ht(l, t, o), e !== null && (gt(e, l, o, i), ll(e, l, o)), o;
  }
  function Pl(e) {
    if (e = e.current, !e.child) return null;
    switch (e.child.tag) {
      case 5:
        return e.child.stateNode;
      default:
        return e.child.stateNode;
    }
  }
  function Ra(e, t) {
    if (e = e.memoizedState, e !== null && e.dehydrated !== null) {
      var n = e.retryLane;
      e.retryLane = n !== 0 && n < t ? n : t;
    }
  }
  function Eo(e, t) {
    Ra(e, t), (e = e.alternate) && Ra(e, t);
  }
  function Jd() {
    return null;
  }
  var Ma = typeof reportError == "function" ? reportError : function(e) {
    console.error(e);
  };
  function No(e) {
    this._internalRoot = e;
  }
  Tl.prototype.render = No.prototype.render = function(e) {
    var t = this._internalRoot;
    if (t === null) throw Error(a(409));
    Nl(e, t, null, null);
  }, Tl.prototype.unmount = No.prototype.unmount = function() {
    var e = this._internalRoot;
    if (e !== null) {
      this._internalRoot = null;
      var t = e.containerInfo;
      on(function() {
        Nl(null, e, null, null);
      }), t[_t] = null;
    }
  };
  function Tl(e) {
    this._internalRoot = e;
  }
  Tl.prototype.unstable_scheduleHydration = function(e) {
    if (e) {
      var t = hs();
      e = { blockedOn: null, target: e, priority: t };
      for (var n = 0; n < It.length && t !== 0 && t < It[n].priority; n++) ;
      It.splice(n, 0, e), n === 0 && ys(e);
    }
  };
  function Po(e) {
    return !(!e || e.nodeType !== 1 && e.nodeType !== 9 && e.nodeType !== 11);
  }
  function Ll(e) {
    return !(!e || e.nodeType !== 1 && e.nodeType !== 9 && e.nodeType !== 11 && (e.nodeType !== 8 || e.nodeValue !== " react-mount-point-unstable "));
  }
  function za() {
  }
  function bd(e, t, n, r, l) {
    if (l) {
      if (typeof r == "function") {
        var i = r;
        r = function() {
          var y = Pl(o);
          i.call(y);
        };
      }
      var o = La(t, r, e, 0, null, !1, !1, "", za);
      return e._reactRootContainer = o, e[_t] = o.current, rr(e.nodeType === 8 ? e.parentNode : e), on(), o;
    }
    for (; l = e.lastChild; ) e.removeChild(l);
    if (typeof r == "function") {
      var u = r;
      r = function() {
        var y = Pl(d);
        u.call(y);
      };
    }
    var d = jo(e, 0, !1, null, null, !1, !1, "", za);
    return e._reactRootContainer = d, e[_t] = d.current, rr(e.nodeType === 8 ? e.parentNode : e), on(function() {
      Nl(t, d, n, r);
    }), d;
  }
  function Rl(e, t, n, r, l) {
    var i = n._reactRootContainer;
    if (i) {
      var o = i;
      if (typeof l == "function") {
        var u = l;
        l = function() {
          var d = Pl(o);
          u.call(d);
        };
      }
      Nl(t, o, e, l);
    } else o = bd(n, t, e, l, r);
    return Pl(o);
  }
  ps = function(e) {
    switch (e.tag) {
      case 3:
        var t = e.stateNode;
        if (t.current.memoizedState.isDehydrated) {
          var n = Hn(t.pendingLanes);
          n !== 0 && (Zl(t, n | 1), Je(t, Ee()), (le & 6) === 0 && (In = Ee() + 500, Vt()));
        }
        break;
      case 13:
        on(function() {
          var r = Nt(e, 1);
          if (r !== null) {
            var l = Ge();
            gt(r, e, 1, l);
          }
        }), Eo(e, 1);
    }
  }, Jl = function(e) {
    if (e.tag === 13) {
      var t = Nt(e, 134217728);
      if (t !== null) {
        var n = Ge();
        gt(t, e, 134217728, n);
      }
      Eo(e, 134217728);
    }
  }, ms = function(e) {
    if (e.tag === 13) {
      var t = Gt(e), n = Nt(e, t);
      if (n !== null) {
        var r = Ge();
        gt(n, e, t, r);
      }
      Eo(e, t);
    }
  }, hs = function() {
    return de;
  }, vs = function(e, t) {
    var n = de;
    try {
      return de = e, t();
    } finally {
      de = n;
    }
  }, Wl = function(e, t, n) {
    switch (t) {
      case "input":
        if (Ol(e, n), t = n.name, n.type === "radio" && t != null) {
          for (n = e; n.parentNode; ) n = n.parentNode;
          for (n = n.querySelectorAll("input[name=" + JSON.stringify("" + t) + '][type="radio"]'), t = 0; t < n.length; t++) {
            var r = n[t];
            if (r !== e && r.form === e.form) {
              var l = Yr(r);
              if (!l) throw Error(a(90));
              Vo(r), Ol(r, l);
            }
          }
        }
        break;
      case "textarea":
        Qo(e, n);
        break;
      case "select":
        t = n.value, t != null && dn(e, !!n.multiple, t, !1);
    }
  }, es = wo, ts = on;
  var ef = { usingClientEntryPoint: !1, Events: [or, kn, Yr, Jo, bo, wo] }, xr = { findFiberByHostInstance: Zt, bundleType: 0, version: "18.3.1", rendererPackageName: "react-dom" }, tf = { bundleType: xr.bundleType, version: xr.version, rendererPackageName: xr.rendererPackageName, rendererConfig: xr.rendererConfig, overrideHookState: null, overrideHookStateDeletePath: null, overrideHookStateRenamePath: null, overrideProps: null, overridePropsDeletePath: null, overridePropsRenamePath: null, setErrorHandler: null, setSuspenseHandler: null, scheduleUpdate: null, currentDispatcherRef: X.ReactCurrentDispatcher, findHostInstanceByFiber: function(e) {
    return e = is(e), e === null ? null : e.stateNode;
  }, findFiberByHostInstance: xr.findFiberByHostInstance || Jd, findHostInstancesForRefresh: null, scheduleRefresh: null, scheduleRoot: null, setRefreshHandler: null, getCurrentFiber: null, reconcilerVersion: "18.3.1-next-f1338f8080-20240426" };
  if (typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ < "u") {
    var Ml = __REACT_DEVTOOLS_GLOBAL_HOOK__;
    if (!Ml.isDisabled && Ml.supportsFiber) try {
      Tr = Ml.inject(tf), yt = Ml;
    } catch {
    }
  }
  return be.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED = ef, be.createPortal = function(e, t) {
    var n = 2 < arguments.length && arguments[2] !== void 0 ? arguments[2] : null;
    if (!Po(t)) throw Error(a(200));
    return Zd(e, t, null, n);
  }, be.createRoot = function(e, t) {
    if (!Po(e)) throw Error(a(299));
    var n = !1, r = "", l = Ma;
    return t != null && (t.unstable_strictMode === !0 && (n = !0), t.identifierPrefix !== void 0 && (r = t.identifierPrefix), t.onRecoverableError !== void 0 && (l = t.onRecoverableError)), t = jo(e, 1, !1, null, null, n, !1, r, l), e[_t] = t.current, rr(e.nodeType === 8 ? e.parentNode : e), new No(t);
  }, be.findDOMNode = function(e) {
    if (e == null) return null;
    if (e.nodeType === 1) return e;
    var t = e._reactInternals;
    if (t === void 0)
      throw typeof e.render == "function" ? Error(a(188)) : (e = Object.keys(e).join(","), Error(a(268, e)));
    return e = is(t), e = e === null ? null : e.stateNode, e;
  }, be.flushSync = function(e) {
    return on(e);
  }, be.hydrate = function(e, t, n) {
    if (!Ll(t)) throw Error(a(200));
    return Rl(null, e, t, !0, n);
  }, be.hydrateRoot = function(e, t, n) {
    if (!Po(e)) throw Error(a(405));
    var r = n != null && n.hydratedSources || null, l = !1, i = "", o = Ma;
    if (n != null && (n.unstable_strictMode === !0 && (l = !0), n.identifierPrefix !== void 0 && (i = n.identifierPrefix), n.onRecoverableError !== void 0 && (o = n.onRecoverableError)), t = La(t, null, e, 1, n ?? null, l, !1, i, o), e[_t] = t.current, rr(e), r) for (e = 0; e < r.length; e++) n = r[e], l = n._getVersion, l = l(n._source), t.mutableSourceEagerHydrationData == null ? t.mutableSourceEagerHydrationData = [n, l] : t.mutableSourceEagerHydrationData.push(
      n,
      l
    );
    return new Tl(t);
  }, be.render = function(e, t, n) {
    if (!Ll(t)) throw Error(a(200));
    return Rl(null, e, t, !1, n);
  }, be.unmountComponentAtNode = function(e) {
    if (!Ll(e)) throw Error(a(40));
    return e._reactRootContainer ? (on(function() {
      Rl(null, null, e, !1, function() {
        e._reactRootContainer = null, e[_t] = null;
      });
    }), !0) : !1;
  }, be.unstable_batchedUpdates = wo, be.unstable_renderSubtreeIntoContainer = function(e, t, n, r) {
    if (!Ll(n)) throw Error(a(200));
    if (e == null || e._reactInternals === void 0) throw Error(a(38));
    return Rl(e, t, n, !1, r);
  }, be.version = "18.3.1-next-f1338f8080-20240426", be;
}
var $a;
function tc() {
  if ($a) return Mo.exports;
  $a = 1;
  function c() {
    if (!(typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ > "u" || typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE != "function"))
      try {
        __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE(c);
      } catch (g) {
        console.error(g);
      }
  }
  return c(), Mo.exports = df(), Mo.exports;
}
var Ha;
function ff() {
  if (Ha) return zl;
  Ha = 1;
  var c = tc();
  return zl.createRoot = c.createRoot, zl.hydrateRoot = c.hydrateRoot, zl;
}
var pf = ff(), mf = tc();
function Do(...c) {
  return c.filter(Boolean).join(" ");
}
function hf({
  content: c,
  children: g,
  className: a,
  triggerClassName: P,
  bubbleClassName: S,
  focusable: D = !1,
  ariaLabel: F,
  inline: B = !0
}) {
  const _ = te.useRef(null), M = te.useRef(null), k = te.useRef(null), [L, w] = te.useState(!1), [E, R] = te.useState({}), [Y, $] = te.useState("bottom"), ae = te.useCallback(() => {
    const X = M.current, J = k.current;
    if (!X || !J || typeof window > "u")
      return;
    const b = 12, Z = 12, ee = X.getBoundingClientRect(), xe = J.getBoundingClientRect(), fe = Math.ceil(xe.height), Oe = Math.ceil(xe.width), Te = ee.left + ee.width / 2 - Oe / 2, je = Math.min(
      Math.max(b, Te),
      Math.max(b, window.innerWidth - Oe - b)
    ), We = ee.bottom + Z + fe + b <= window.innerHeight || ee.top < fe + Z + b ? "bottom" : "top", Le = We === "bottom" ? ee.bottom + Z : Math.max(b, ee.top - fe - Z), ue = ee.left + ee.width / 2, z = Math.min(
      Math.max(18, ue - je),
      Math.max(18, Oe - 18)
    );
    $(We), R({
      left: je,
      top: Le,
      "--admin-tooltip-arrow-left": `${z}px`
    });
  }, []);
  te.useLayoutEffect(() => {
    if (!L)
      return;
    ae();
    const X = () => ae();
    return window.addEventListener("resize", X), window.addEventListener("scroll", X, !0), () => {
      window.removeEventListener("resize", X), window.removeEventListener("scroll", X, !0);
    };
  }, [ae, L]);
  const ge = B ? "span" : "div", _e = B ? "span" : "div";
  return /* @__PURE__ */ s.jsxs(
    ge,
    {
      className: Do("admin-tooltip", a),
      onMouseEnter: () => w(!0),
      onMouseLeave: () => w(!1),
      onFocusCapture: () => w(!0),
      onBlurCapture: (X) => {
        var J;
        (J = _.current) != null && J.contains(X.relatedTarget) || w(!1);
      },
      ref: (X) => {
        _.current = X;
      },
      children: [
        /* @__PURE__ */ s.jsx(
          _e,
          {
            className: Do("admin-tooltip-trigger", P),
            ...D ? { tabIndex: 0, "aria-label": F } : {},
            ref: (X) => {
              M.current = X;
            },
            children: g
          }
        ),
        typeof document < "u" ? mf.createPortal(
          /* @__PURE__ */ s.jsx(
            "span",
            {
              ref: k,
              className: Do("admin-tooltip-bubble", `placement-${Y}`, S, L && "visible"),
              role: "tooltip",
              style: E,
              children: c
            }
          ),
          document.body
        ) : null
      ]
    }
  );
}
function vf(c) {
  return typeof c == "object" && c !== null && !Array.isArray(c);
}
function gf(c) {
  var a;
  const g = typeof ((a = globalThis.crypto) == null ? void 0 : a.randomUUID) == "function" ? globalThis.crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${c}-${g}`;
}
function Uo(c) {
  const g = /* @__PURE__ */ new Set(), a = [];
  for (const P of c) {
    const S = P.trim();
    !S || g.has(S) || (g.add(S), a.push(S));
  }
  return a;
}
function Ba(c) {
  return Uo(c).join(`
`);
}
function nc(c = "", g = "") {
  return {
    id: gf("map-row"),
    key: c,
    value: g
  };
}
function Wa(c) {
  return vf(c) ? Object.entries(c).map(([g, a]) => nc(g, String(a ?? ""))) : [];
}
function Qa(c, g, a = !1) {
  const P = {}, S = /* @__PURE__ */ new Set();
  for (const D of c) {
    const F = D.key.trim(), B = D.value;
    if (!(!F && !B.trim())) {
      if (!F)
        throw new Error(`${g} contains a value without a key.`);
      if (S.has(F))
        throw new Error(`${g} contains duplicate key '${F}'.`);
      S.add(F), P[F] = B;
    }
  }
  if (!(Object.keys(P).length === 0 && !a))
    return P;
}
function Oo(c = "") {
  return {
    id: `string-row-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    value: c
  };
}
function Ga(c, g) {
  const a = c.map((P) => g ? g(P) : P);
  return Uo(a);
}
function rc(c, g) {
  return /* @__PURE__ */ s.jsx("div", { className: "empty-state", children: c ?? g });
}
function lc(c, g, a) {
  if (c.current === a) return !1;
  const P = g.current === a;
  return c.current = a, P ? (g.current = null, !1) : (g.current = null, !0);
}
function Ka({
  values: c,
  onChange: g,
  disabled: a = !1,
  className: P,
  description: S,
  emptyState: D,
  addLabel: F = "Add item",
  itemLabel: B = "Value",
  placeholder: _ = "Enter a value",
  suggestions: M,
  validateItem: k,
  normalizeItem: L
}) {
  const w = te.useId(), E = Ga(c, L), R = Ba(E), [Y, $] = te.useState(() => E.map((Z) => Oo(Z))), ae = te.useRef(R), ge = te.useRef(null);
  te.useEffect(() => {
    lc(ae, ge, R) && $(E.map((Z) => Oo(Z)));
  }, [E, R]);
  function _e(Z) {
    const ee = Ga(
      Z.map((xe) => xe.value),
      L
    );
    ge.current = Ba(ee), g(ee);
  }
  const J = (M ?? []).map((Z) => String(Z)), b = J.length > 0 ? Uo(J) : [];
  return /* @__PURE__ */ s.jsxs("div", { className: ["string-list-selector", "structured-input-editor", P].filter(Boolean).join(" "), children: [
    S ? /* @__PURE__ */ s.jsx("div", { className: "structured-input-description", children: S }) : null,
    /* @__PURE__ */ s.jsx("div", { className: "inline-actions compact-actions", children: /* @__PURE__ */ s.jsx(
      "button",
      {
        type: "button",
        onClick: () => {
          const Z = [...Y, Oo()];
          $(Z);
        },
        disabled: a,
        children: F
      }
    ) }),
    Y.length === 0 ? rc(D, `No ${B.toLowerCase()}s are configured.`) : null,
    Y.map((Z, ee) => {
      const xe = (k == null ? void 0 : k(Z.value, ee, Y.map((fe) => fe.value))) ?? null;
      return /* @__PURE__ */ s.jsxs("div", { className: "string-list-selector-row structured-input-row", children: [
        /* @__PURE__ */ s.jsxs("label", { className: "field-stack structured-input-field", children: [
          /* @__PURE__ */ s.jsx("span", { children: B }),
          /* @__PURE__ */ s.jsx(
            "input",
            {
              list: b.length > 0 ? w : void 0,
              value: Z.value,
              onChange: (fe) => {
                const Oe = L ? L(fe.target.value) : fe.target.value, Te = Y.map(
                  (je, Ae) => Ae === ee ? { ...je, value: Oe } : je
                );
                $(Te), _e(Te);
              },
              placeholder: _,
              disabled: a
            }
          ),
          xe ? /* @__PURE__ */ s.jsx("div", { className: "structured-input-error", children: xe }) : null
        ] }),
        /* @__PURE__ */ s.jsx("div", { className: "inline-actions compact-actions", children: /* @__PURE__ */ s.jsx(
          "button",
          {
            type: "button",
            className: "danger",
            onClick: () => {
              const fe = Y.filter((Oe, Te) => Te !== ee);
              $(fe), _e(fe);
            },
            disabled: a,
            children: "Remove"
          }
        ) })
      ] }, Z.id);
    }),
    b.length > 0 ? /* @__PURE__ */ s.jsx("datalist", { id: w, children: b.map((Z) => /* @__PURE__ */ s.jsx("option", { value: Z }, Z)) }) : null
  ] });
}
function yf({
  value: c,
  onChange: g,
  onValidationChange: a,
  fieldLabel: P,
  disabled: S = !1,
  className: D,
  description: F,
  emptyState: B,
  addLabel: _ = "Add row",
  keyLabel: M = "Key",
  valueLabel: k = "Value"
}) {
  const L = JSON.stringify(c ?? {}), [w, E] = te.useState(() => Wa(c)), [R, Y] = te.useState(null), $ = te.useRef(L), ae = te.useRef(null);
  te.useEffect(() => {
    lc($, ae, L) && (E(Wa(c)), Y(null));
  }, [L, c]);
  function ge(X) {
    try {
      const b = Qa(X, P, !1) ?? {};
      ae.current = JSON.stringify(b), g(b), Y(null);
    } catch (J) {
      Y(J instanceof Error ? J.message : String(J));
    }
  }
  const _e = R ?? (() => {
    try {
      return Qa(w, P, !1), null;
    } catch (X) {
      return X instanceof Error ? X.message : String(X);
    }
  })();
  return te.useEffect(() => {
    a == null || a(_e);
  }, [a, _e]), /* @__PURE__ */ s.jsxs("div", { className: ["structured-input-editor", D].filter(Boolean).join(" "), children: [
    F ? /* @__PURE__ */ s.jsx("div", { className: "structured-input-description", children: F }) : null,
    /* @__PURE__ */ s.jsx("div", { className: "inline-actions compact-actions", children: /* @__PURE__ */ s.jsx(
      "button",
      {
        type: "button",
        onClick: () => E((X) => [...X, nc()]),
        disabled: S,
        children: _
      }
    ) }),
    R ? /* @__PURE__ */ s.jsx("div", { className: "callout error-callout", children: R }) : null,
    w.length === 0 ? rc(B, `No ${P.toLowerCase()} rows are configured.`) : null,
    w.map((X, J) => /* @__PURE__ */ s.jsxs("div", { className: "string-list-selector-row structured-input-row structured-input-map-row", children: [
      /* @__PURE__ */ s.jsxs("label", { className: "field-stack structured-input-field", children: [
        /* @__PURE__ */ s.jsx("span", { children: M }),
        /* @__PURE__ */ s.jsx(
          "input",
          {
            value: X.key,
            onChange: (b) => {
              const Z = w.map(
                (ee, xe) => xe === J ? { ...ee, key: b.target.value } : ee
              );
              E(Z), ge(Z);
            },
            disabled: S
          }
        )
      ] }),
      /* @__PURE__ */ s.jsxs("label", { className: "field-stack structured-input-field", children: [
        /* @__PURE__ */ s.jsx("span", { children: k }),
        /* @__PURE__ */ s.jsx(
          "input",
          {
            value: X.value,
            onChange: (b) => {
              const Z = w.map(
                (ee, xe) => xe === J ? { ...ee, value: b.target.value } : ee
              );
              E(Z), ge(Z);
            },
            disabled: S
          }
        )
      ] }),
      /* @__PURE__ */ s.jsx("div", { className: "inline-actions compact-actions", children: /* @__PURE__ */ s.jsx(
        "button",
        {
          type: "button",
          className: "danger",
          onClick: () => {
            const b = w.filter((Z, ee) => ee !== J);
            E(b), ge(b);
          },
          disabled: S,
          children: "Remove"
        }
      ) })
    ] }, X.id))
  ] });
}
const wf = "";
class xf extends Error {
  constructor(a, P, S) {
    super(a);
    To(this, "status");
    To(this, "payload");
    this.name = "ApiError", this.status = P, this.payload = S;
  }
}
async function Il(c, g) {
  const a = await fetch(`${wf}${c}`, {
    ...g,
    headers: {
      "Content-Type": "application/json",
      ...(g == null ? void 0 : g.headers) ?? {}
    }
  }), P = a.headers.get("content-type") ?? "";
  let S;
  if (P.includes("application/json"))
    S = await a.json();
  else {
    const D = await a.text();
    throw new Error(D.trim() || `${a.status} ${a.statusText}`);
  }
  if (!a.ok && typeof S == "object" && S && "error" in S)
    throw new xf(String(S.error), a.status, S);
  return S;
}
let Ya = 1;
function kf(c) {
  const a = `${c.trim() || "target"}-${Ya}`;
  return Ya += 1, a;
}
function Sf() {
  return {
    id: kf("target"),
    alias: "",
    baseUrl: "",
    metadata: {}
  };
}
function ic(c, g = null) {
  if (g) return g;
  const a = /* @__PURE__ */ new Set();
  for (const P of c) {
    const S = P.alias.trim();
    if (!S) return "HTTP target aliases must be non-empty.";
    if (a.has(S)) return `Duplicate HTTP target alias: ${S}`;
    a.add(S);
    const D = P.baseUrl.trim();
    if (!D) return `HTTP target ${S} must define an allowed base URL.`;
    try {
      const F = new URL(D);
      if (F.protocol !== "http:" && F.protocol !== "https:")
        return `HTTP target ${S} must use an http:// or https:// URL.`;
    } catch {
      return `HTTP target ${S} must use a valid HTTP(S) URL.`;
    }
  }
  return null;
}
function _f(c) {
  const g = ic(c);
  if (g) throw new Error(g);
  const a = {};
  for (const P of c) {
    const S = { ...P.metadata };
    a[P.alias.trim()] = {
      base_url: P.baseUrl.trim().replace(/\/+$/, ""),
      ...P.managedWebsiteSession ? { managed_website_session: P.managedWebsiteSession } : {},
      ...Object.keys(S).length > 0 ? { metadata: S } : {}
    };
  }
  return { targets: a };
}
function qa() {
  return {
    backend: "libvirt_vm",
    vmName: "",
    targetName: "",
    sessionId: "",
    ttlDuration: "7d",
    model: "",
    provider: "",
    vmUser: "agent",
    agentProvider: "",
    modelProviderMode: "",
    codexAuthMode: "",
    subscriptionProfileId: "",
    vmImage: "",
    containerImage: "",
    workerId: "",
    dotnet: !1,
    nuget: !1,
    mssql: !1,
    mssqlInitRoot: "",
    playwright: !1,
    initialPolicy: Cf()
  };
}
function Cf() {
  return {
    labels: {},
    webSearchEnabled: !1,
    allowedTools: [],
    connectorGrants: {},
    hostDirectoryGrants: {},
    allowedScriptNames: [],
    communicationTargetIds: [],
    emailMailboxGrants: [],
    httpTargets: []
  };
}
function cn(c) {
  const g = /* @__PURE__ */ new Set(), a = [];
  for (const P of c) {
    const S = P.trim();
    !S || g.has(S) || (g.add(S), a.push(S));
  }
  return a;
}
function Ke(c) {
  return c.trim() || null;
}
function oc(c) {
  const g = c.trim().toLowerCase().match(
    /^(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks)$/
  );
  if (!g) return null;
  const a = Number(g[1]);
  if (!Number.isInteger(a) || a <= 0) return null;
  const P = {
    s: 1,
    sec: 1,
    secs: 1,
    second: 1,
    seconds: 1,
    m: 60,
    min: 60,
    mins: 60,
    minute: 60,
    minutes: 60,
    h: 3600,
    hr: 3600,
    hrs: 3600,
    hour: 3600,
    hours: 3600,
    d: 1440 * 60,
    day: 1440 * 60,
    days: 1440 * 60,
    w: 10080 * 60,
    week: 10080 * 60,
    weeks: 10080 * 60
  };
  return a * P[g[2]];
}
function jf(c, g) {
  const a = {
    backend: c.backend,
    session_id: Ke(c.sessionId),
    directory_path: Ke(g)
  }, P = oc(c.ttlDuration);
  P != null && (a.ttl_seconds = P);
  const S = Ke(c.model);
  S && (a.model = S);
  const D = Ke(c.agentProvider);
  D && (a.agent_provider = D);
  const F = Ke(c.modelProviderMode);
  F && (a.model_provider_mode = F);
  const B = Ke(c.codexAuthMode);
  if ((B === "metered_api" || B === "chatgpt_subscription") && (a.codex_auth_mode = B), B === "chatgpt_subscription" && (a.subscription_profile_id = Ke(c.subscriptionProfileId)), a.dotnet = c.dotnet, a.nuget = c.nuget, a.playwright = c.playwright, c.backend === "libvirt_vm") {
    const w = Ke(c.provider);
    a.vm_name = Ke(c.vmName), w && (a.provider = w), a.vm_user = Ke(c.vmUser) ?? "agent";
    const E = Ke(c.vmImage);
    E && (a.vm_image = E), a.mssql = c.mssql, a.mssql_init_root = c.mssql ? Ke(c.mssqlInitRoot) : null;
  } else {
    a.target_name = Ke(c.targetName), a.container_image = Ke(c.containerImage);
    const w = Ke(c.workerId);
    w && (a.worker_id = w);
  }
  const _ = c.initialPolicy;
  Object.keys(_.labels).length > 0 && (a.labels = _.labels), _.webSearchEnabled && (a.web_search_enabled = !0);
  const M = cn(_.allowedTools);
  M.length > 0 && (a.allowed_tools = M), Object.keys(_.connectorGrants).length > 0 && (a.connector_grants = _.connectorGrants), Object.keys(_.hostDirectoryGrants).length > 0 && (a.host_directory_grants = _.hostDirectoryGrants);
  const k = cn(_.allowedScriptNames);
  k.length > 0 && (a.allowed_script_names = k);
  const L = cn(_.communicationTargetIds);
  return a.communication_target_ids = L, a.email_mailbox_grants = _.emailMailboxGrants.map((w) => ({
    mailbox_id: w.mailbox_id,
    access: w.access,
    folders: cn(w.folders)
  })), _.httpTargets.length > 0 && (a.http_targets = _f(_.httpTargets)), a;
}
function Ef(c) {
  return c.vmName.trim() || c.targetName.trim() || c.sessionId.trim() || "agent";
}
async function Nf(c, g = "", a = "") {
  const P = new URLSearchParams(), S = c.trim();
  S && P.set("directory_path", S), g.trim() && P.set("worker_id", g.trim()), a.trim() && P.set("session_id", a.trim());
  const D = P.toString();
  return Il(
    `/api/admin/agents/create-options${D ? `?${D}` : ""}`
  );
}
async function Pf(c) {
  return (await Il(
    "/api/admin/agents/create-operations",
    {
      method: "POST",
      body: JSON.stringify(c)
    }
  )).operation;
}
async function Tf(c) {
  return (await Il(
    `/api/admin/agents/create-operations/${encodeURIComponent(c)}`
  )).operation;
}
function Lf(c) {
  return c.status === "queued" || c.status === "running";
}
function Rf(c) {
  var P;
  const g = (P = c.session_id) == null ? void 0 : P.trim();
  if (g) return g;
  const a = c.result;
  if (!a) return null;
  for (const S of ["session_id", "sessionId"]) {
    const D = a[S];
    if (typeof D == "string" && D.trim()) return D.trim();
  }
  for (const S of ["session", "agent"]) {
    const D = a[S];
    if (typeof D != "object" || D === null || Array.isArray(D)) continue;
    const F = D.session_id;
    if (typeof F == "string" && F.trim()) return F.trim();
  }
  return null;
}
function Sr(c) {
  return [...new Set(c.map((g) => g.trim()).filter(Boolean))];
}
function Xa(c, g) {
  var P;
  const a = (P = c.metadata) == null ? void 0 : P[g];
  return typeof a == "string" ? a.trim() : "";
}
function Mf(c, g) {
  var P;
  const a = (P = c.metadata) == null ? void 0 : P[g];
  return Array.isArray(a) ? a.map((S) => typeof S == "string" ? S.trim() : "").filter(Boolean) : [];
}
function Za(c, g = "Unknown") {
  return c.trim() ? c.trim().replace(/[_-]+/g, " ").replace(/\s+/g, " ").replace(/\b\w/g, (a) => a.toUpperCase()) : g;
}
function zf({
  values: c,
  options: g,
  onChange: a,
  emptyState: P,
  disabled: S = !1
}) {
  const D = Sr(c), F = new Set(D), B = new Set(g.map((M) => M.value)), _ = [
    ...g.map((M) => {
      var k;
      return {
        value: M.value,
        detail: ((k = M.description) == null ? void 0 : k.trim()) || (M.label !== M.value ? M.label : ""),
        provider: Za(Xa(M, "provider")),
        shape: Za(Xa(M, "target_shape")),
        capabilities: Mf(M, "policy_capabilities"),
        unknown: !1
      };
    }),
    ...D.filter((M) => !B.has(M)).map((M) => ({
      value: M,
      detail: "Selected target is no longer in the directory options.",
      provider: "Unknown",
      shape: "Unknown",
      capabilities: [],
      unknown: !0
    }))
  ];
  return /* @__PURE__ */ s.jsxs("div", { className: "agent-communication-target-selector", children: [
    _.length > 0 ? /* @__PURE__ */ s.jsx("div", { className: "agent-communication-target-table-wrap", children: /* @__PURE__ */ s.jsxs("table", { className: "agent-communication-target-table", children: [
      /* @__PURE__ */ s.jsx("thead", { children: /* @__PURE__ */ s.jsxs("tr", { children: [
        /* @__PURE__ */ s.jsx("th", { children: "Select" }),
        /* @__PURE__ */ s.jsx("th", { children: "Target" }),
        /* @__PURE__ */ s.jsx("th", { children: "Provider" }),
        /* @__PURE__ */ s.jsx("th", { children: "Shape" }),
        /* @__PURE__ */ s.jsx("th", { children: "Capabilities" })
      ] }) }),
      /* @__PURE__ */ s.jsx("tbody", { children: _.map((M) => {
        const k = F.has(M.value);
        return /* @__PURE__ */ s.jsxs("tr", { className: k ? "selected" : "", children: [
          /* @__PURE__ */ s.jsx("td", { className: "agent-communication-target-select-cell", children: /* @__PURE__ */ s.jsx("input", { type: "checkbox", disabled: S, checked: k, onChange: (L) => a(L.target.checked ? Sr([...D, M.value]) : D.filter((w) => w !== M.value)), "aria-label": `Allow communication target ${M.value}` }) }),
          /* @__PURE__ */ s.jsx("td", { children: /* @__PURE__ */ s.jsxs("div", { className: "agent-communication-target-name", children: [
            /* @__PURE__ */ s.jsx("strong", { children: M.value }),
            M.detail ? /* @__PURE__ */ s.jsx("span", { children: M.detail }) : null,
            M.unknown ? /* @__PURE__ */ s.jsx("span", { className: "agent-communication-target-warning", children: "Unknown target" }) : null
          ] }) }),
          /* @__PURE__ */ s.jsx("td", { children: M.provider }),
          /* @__PURE__ */ s.jsx("td", { children: M.shape }),
          /* @__PURE__ */ s.jsx("td", { children: M.capabilities.length ? /* @__PURE__ */ s.jsx("span", { className: "agent-communication-target-capabilities", children: M.capabilities.map((L) => /* @__PURE__ */ s.jsx("span", { children: L }, L)) }) : /* @__PURE__ */ s.jsx("span", { className: "muted-text", children: "None" }) })
        ] }, M.value);
      }) })
    ] }) }) : null,
    D.length === 0 ? /* @__PURE__ */ s.jsx("div", { className: "empty-state", children: P }) : null
  ] });
}
function If({
  grants: c,
  options: g,
  onChange: a,
  emptyState: P,
  disabled: S = !1
}) {
  const D = new Map(c.map((k) => [k.mailbox_id, k])), F = new Map(g.map((k) => [k.mailbox_id, k])), B = [
    ...g,
    ...c.filter((k) => !F.has(k.mailbox_id)).map((k) => ({
      mailbox_id: k.mailbox_id,
      provider: "unknown",
      account_identifier: "Mailbox is no longer configured",
      folders: k.folders,
      default_folders: k.folders,
      send_enabled: !1
    }))
  ];
  function _(k, L) {
    a([
      ...c.filter((w) => w.mailbox_id !== k),
      ...L ? [L] : []
    ]);
  }
  function M(k, L) {
    const w = D.get(k.mailbox_id), E = Sr([
      ...(w == null ? void 0 : w.folders) ?? k.default_folders,
      ...L === "read" ? [] : ["drafts"]
    ]);
    _(k.mailbox_id, { mailbox_id: k.mailbox_id, access: L, folders: E });
  }
  return /* @__PURE__ */ s.jsxs("div", { className: "agent-communication-target-selector", children: [
    B.length ? /* @__PURE__ */ s.jsx("div", { className: "agent-communication-target-table-wrap", children: /* @__PURE__ */ s.jsxs("table", { className: "agent-communication-target-table mailbox-grant-table", children: [
      /* @__PURE__ */ s.jsx("thead", { children: /* @__PURE__ */ s.jsxs("tr", { children: [
        /* @__PURE__ */ s.jsx("th", { children: "Select" }),
        /* @__PURE__ */ s.jsx("th", { children: "Mailbox account" }),
        /* @__PURE__ */ s.jsx("th", { children: "Access" }),
        /* @__PURE__ */ s.jsx("th", { children: "Folders" })
      ] }) }),
      /* @__PURE__ */ s.jsx("tbody", { children: B.map((k) => {
        const L = D.get(k.mailbox_id), w = new Set((L == null ? void 0 : L.folders) ?? []), E = k.folders.includes("drafts");
        return /* @__PURE__ */ s.jsxs("tr", { className: L ? "selected" : "", children: [
          /* @__PURE__ */ s.jsx("td", { className: "agent-communication-target-select-cell", children: /* @__PURE__ */ s.jsx("input", { type: "checkbox", disabled: S, checked: !!L, onChange: (R) => _(k.mailbox_id, R.target.checked ? { mailbox_id: k.mailbox_id, access: "read", folders: Sr(k.default_folders) } : null), "aria-label": `Grant mailbox ${k.mailbox_id}` }) }),
          /* @__PURE__ */ s.jsx("td", { children: /* @__PURE__ */ s.jsxs("div", { className: "agent-communication-target-name", children: [
            /* @__PURE__ */ s.jsx("strong", { children: k.mailbox_id }),
            /* @__PURE__ */ s.jsxs("span", { children: [
              k.account_identifier,
              " · ",
              k.provider
            ] }),
            F.has(k.mailbox_id) ? null : /* @__PURE__ */ s.jsx("span", { className: "agent-communication-target-warning", children: "Unknown mailbox" })
          ] }) }),
          /* @__PURE__ */ s.jsx("td", { children: /* @__PURE__ */ s.jsxs("select", { value: (L == null ? void 0 : L.access) ?? "read", disabled: S || !L, onChange: (R) => M(k, R.target.value), children: [
            /* @__PURE__ */ s.jsx("option", { value: "read", children: "Read only" }),
            /* @__PURE__ */ s.jsx("option", { value: "draft", disabled: !E, children: "Read + draft" }),
            /* @__PURE__ */ s.jsx("option", { value: "send", disabled: !E || !k.send_enabled, children: "Read + draft + send" })
          ] }) }),
          /* @__PURE__ */ s.jsx("td", { children: /* @__PURE__ */ s.jsx("div", { className: "mailbox-folder-options", children: k.folders.map((R) => /* @__PURE__ */ s.jsxs("label", { children: [
            /* @__PURE__ */ s.jsx("input", { type: "checkbox", disabled: S || !L || (L.access === "draft" || L.access === "send") && R === "drafts", checked: w.has(R), onChange: (Y) => L && _(k.mailbox_id, { ...L, folders: Y.target.checked ? Sr([...L.folders, R]) : L.folders.filter(($) => $ !== R) }) }),
            R
          ] }, R)) }) })
        ] }, k.mailbox_id);
      }) })
    ] }) }) : null,
    c.length === 0 ? /* @__PURE__ */ s.jsx("div", { className: "empty-state", children: P }) : null
  ] });
}
function Df({
  entries: c = [],
  onChange: g,
  onValidationChange: a,
  disabled: P = !1,
  sourceError: S = null,
  sessionId: D = null,
  directoryPath: F
}) {
  const B = ic(c, S), [_, M] = te.useState([]), [k, L] = te.useState(null);
  te.useEffect(() => {
    if (M([]), L(null), !F) return;
    const E = new AbortController();
    return Il(
      `/api/managed-website-sessions?directory_path=${encodeURIComponent(F)}`,
      { signal: E.signal }
    ).then((R) => {
      E.signal.aborted || M(R.profiles ?? []);
    }).catch(() => {
      E.signal.aborted || L("Managed login profiles are unavailable. Existing bindings are preserved.");
    }), () => E.abort();
  }, [F]), te.useEffect(() => {
    a == null || a(B);
  }, [a, B]);
  function w(E, R) {
    g(c.map((Y) => Y.id === E ? { ...Y, ...R } : Y));
  }
  return /* @__PURE__ */ s.jsxs("div", { className: "structured-input-editor http-targets-editor", children: [
    /* @__PURE__ */ s.jsx("p", { className: "field-description", children: "Allow this session to reach specific HTTP(S) base URLs through the session relay. This is separate from Web search. Select a managed login profile to let the host authenticate the browser. Existing workflow metadata is preserved while an alias remains unchanged." }),
    B ? /* @__PURE__ */ s.jsx("div", { className: "callout error-callout", children: B }) : null,
    k ? /* @__PURE__ */ s.jsx("div", { className: "callout", children: k }) : null,
    /* @__PURE__ */ s.jsx("div", { className: "inline-actions compact-actions", children: /* @__PURE__ */ s.jsx(
      "button",
      {
        type: "button",
        onClick: () => g([...c, Sf()]),
        disabled: P,
        children: "Add target"
      }
    ) }),
    c.length === 0 ? /* @__PURE__ */ s.jsx("div", { className: "empty-state", children: "No external HTTP targets are granted." }) : null,
    c.map((E) => {
      const R = E.alias.trim(), Y = R && D ? `$HTTP_TARGET_RELAY_BASE_URL/session/${D}/target/${encodeURIComponent(R)}/` : null;
      return /* @__PURE__ */ s.jsxs("div", { className: "structured-input-json-row http-target-row", children: [
        /* @__PURE__ */ s.jsxs("div", { className: "string-list-selector-row structured-input-row structured-input-map-row", children: [
          /* @__PURE__ */ s.jsxs("label", { className: "field-stack structured-input-field", children: [
            /* @__PURE__ */ s.jsx("span", { children: "Alias" }),
            /* @__PURE__ */ s.jsx(
              "input",
              {
                value: E.alias,
                onChange: ($) => {
                  const ae = $.target.value;
                  w(E.id, {
                    alias: ae,
                    ...ae.trim() !== E.alias.trim() ? { metadata: {} } : {}
                  });
                },
                placeholder: "service-api",
                disabled: P
              }
            )
          ] }),
          /* @__PURE__ */ s.jsxs("label", { className: "field-stack structured-input-field", children: [
            /* @__PURE__ */ s.jsx("span", { children: "Allowed base URL" }),
            /* @__PURE__ */ s.jsx(
              "input",
              {
                value: E.baseUrl,
                onChange: ($) => w(E.id, { baseUrl: $.target.value }),
                placeholder: "https://api.example.com/v1",
                spellCheck: !1,
                disabled: P
              }
            )
          ] }),
          /* @__PURE__ */ s.jsx("div", { className: "inline-actions compact-actions", children: /* @__PURE__ */ s.jsx(
            "button",
            {
              type: "button",
              className: "danger",
              onClick: () => g(c.filter(($) => $.id !== E.id)),
              disabled: P,
              children: "Remove"
            }
          ) })
        ] }),
        /* @__PURE__ */ s.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ s.jsx("span", { children: "Managed website login" }),
          /* @__PURE__ */ s.jsxs(
            "select",
            {
              "aria-label": "Managed website login",
              value: E.managedWebsiteSession ?? "",
              disabled: P,
              onChange: ($) => w(E.id, { managedWebsiteSession: $.target.value }),
              children: [
                /* @__PURE__ */ s.jsx("option", { value: "", children: "None — application authentication supplied by the agent" }),
                E.managedWebsiteSession && !_.some(($) => $.id === E.managedWebsiteSession) ? /* @__PURE__ */ s.jsxs("option", { value: E.managedWebsiteSession, children: [
                  E.managedWebsiteSession,
                  " (unavailable; retained)"
                ] }) : null,
                _.map(($) => {
                  let ae = !1;
                  try {
                    ae = new URL(E.baseUrl).origin === $.profile.browser_origin;
                  } catch {
                  }
                  return /* @__PURE__ */ s.jsxs("option", { value: $.id, disabled: !$.profile.enabled || !ae, children: [
                    $.id,
                    $.profile.enabled ? ae ? "" : " (different website origin)" : " (disabled)"
                  ] }, $.id);
                })
              ]
            }
          ),
          /* @__PURE__ */ s.jsx("span", { className: "field-description", children: "Profiles are managed in Configuration → Access, approvals & audit → Website logins. The website origin must match this target." })
        ] }),
        Y ? /* @__PURE__ */ s.jsxs("div", { className: "field-description", children: [
          "Agent relay path: ",
          /* @__PURE__ */ s.jsx("code", { children: Y })
        ] }) : null
      ] }, E.id);
    })
  ] });
}
const Ja = {
  list: "host_directory_list",
  import: "host_directory_import",
  delete: "host_directory_delete"
}, Of = {
  list: "Browse approved files and folders without exposing the host path.",
  import: "Copy a file or folder into the session as an artifact.",
  delete: "Move a file or folder into the host-side quarantine."
};
function Af(c) {
  const g = /* @__PURE__ */ new Set();
  for (const a of Object.values(c))
    for (const P of a)
      P in Ja && g.add(Ja[P]);
  return [...g];
}
function Ff({
  values: c,
  options: g,
  onChange: a,
  disabled: P = !1
}) {
  function S(_, M, k) {
    const L = c[_] ?? [], w = k ? [.../* @__PURE__ */ new Set([...L, M])] : L.filter((R) => R !== M), E = { ...c };
    w.length ? E[_] = w : delete E[_], a(E);
  }
  const D = new Map(
    g.map((_) => [_.directory_id, _])
  ), F = Object.entries(c).flatMap(
    ([_, M]) => {
      const k = D.get(_), L = new Set((k == null ? void 0 : k.capabilities) ?? []), w = M.filter(
        (E) => !L.has(E)
      );
      return w.length ? [{
        directoryId: _,
        displayName: (k == null ? void 0 : k.display_name) ?? _,
        capabilities: w
      }] : [];
    }
  );
  function B(_, M) {
    const k = new Set(M), L = (c[_] ?? []).filter(
      (E) => !k.has(E)
    ), w = { ...c };
    L.length ? w[_] = L : delete w[_], a(w);
  }
  return /* @__PURE__ */ s.jsxs("div", { className: "detail-stack host-directory-grant-selector", children: [
    g.length ? null : /* @__PURE__ */ s.jsx("div", { className: "empty-state", children: "No host directories are configured for this directory." }),
    g.map((_) => {
      var M;
      return /* @__PURE__ */ s.jsxs("details", { className: "compact-disclosure", children: [
        /* @__PURE__ */ s.jsxs("summary", { className: "compact-disclosure-summary", children: [
          /* @__PURE__ */ s.jsx("span", { children: _.display_name }),
          /* @__PURE__ */ s.jsxs("span", { className: "panel-meta", children: [
            ((M = c[_.directory_id]) == null ? void 0 : M.length) ?? 0,
            " granted"
          ] })
        ] }),
        _.description ? /* @__PURE__ */ s.jsx("p", { className: "muted-text", children: _.description }) : null,
        /* @__PURE__ */ s.jsx("div", { className: "feature-list compact-list", children: _.capabilities.map((k) => /* @__PURE__ */ s.jsxs("label", { className: "session-tool-row", children: [
          /* @__PURE__ */ s.jsxs("span", { className: "session-tool-row-main", children: [
            /* @__PURE__ */ s.jsx(
              "input",
              {
                type: "checkbox",
                checked: (c[_.directory_id] ?? []).includes(k),
                onChange: (L) => S(_.directory_id, k, L.target.checked),
                disabled: P
              }
            ),
            /* @__PURE__ */ s.jsxs("span", { children: [
              /* @__PURE__ */ s.jsx("strong", { children: k }),
              /* @__PURE__ */ s.jsxs("span", { className: "muted-text", children: [
                " ",
                Of[k]
              ] })
            ] })
          ] }),
          k === "delete" && _.delete_requires_approval ? /* @__PURE__ */ s.jsx("span", { className: "status-pill state-waiting", children: "Approval" }) : null
        ] }, k)) })
      ] }, _.directory_id);
    }),
    F.length ? /* @__PURE__ */ s.jsxs("div", { className: "field-stack host-directory-unavailable-grants", children: [
      /* @__PURE__ */ s.jsx("strong", { children: "Unavailable grants" }),
      /* @__PURE__ */ s.jsx("p", { className: "muted-text", children: "These grants refer to a host directory or capability that is no longer available. Revoke them before saving other policy changes." }),
      /* @__PURE__ */ s.jsx("div", { className: "feature-list compact-list", children: F.map((_) => /* @__PURE__ */ s.jsxs("div", { className: "session-tool-row", children: [
        /* @__PURE__ */ s.jsx("span", { className: "session-tool-row-main", children: /* @__PURE__ */ s.jsxs("span", { children: [
          /* @__PURE__ */ s.jsx("strong", { children: _.displayName }),
          /* @__PURE__ */ s.jsxs("span", { className: "muted-text", children: [
            " ",
            _.capabilities.join(", ")
          ] })
        ] }) }),
        /* @__PURE__ */ s.jsx(
          "button",
          {
            type: "button",
            className: "admin-action-button compact",
            onClick: () => B(_.directoryId, _.capabilities),
            disabled: P,
            children: "Revoke unavailable"
          }
        )
      ] }, _.directoryId)) })
    ] }) : null
  ] });
}
function se({ children: c, tooltip: g }) {
  const a = typeof c == "string" ? c : "Field";
  return /* @__PURE__ */ s.jsxs("span", { className: "config-field-label", children: [
    /* @__PURE__ */ s.jsx("span", { children: c }),
    /* @__PURE__ */ s.jsx(
      hf,
      {
        content: g,
        focusable: !0,
        ariaLabel: `${a} help`,
        triggerClassName: "config-field-tooltip-trigger",
        children: /* @__PURE__ */ s.jsx("span", { className: "config-field-tooltip-icon", "aria-hidden": "true", children: "i" })
      }
    )
  ] });
}
function Uf({
  values: c,
  options: g,
  onChange: a
}) {
  function P(S, D, F) {
    const B = c[S] ?? [], _ = F ? cn([...B, D]) : B.filter((k) => k !== D), M = { ...c };
    _.length ? M[S] = _ : delete M[S], a(M);
  }
  return g.length ? /* @__PURE__ */ s.jsx("div", { className: "detail-stack", children: g.map((S) => {
    var D;
    return /* @__PURE__ */ s.jsxs("details", { className: "compact-disclosure", children: [
      /* @__PURE__ */ s.jsxs("summary", { className: "compact-disclosure-summary", children: [
        /* @__PURE__ */ s.jsx("span", { children: S.display_name }),
        /* @__PURE__ */ s.jsxs("span", { className: "panel-meta", children: [
          ((D = c[S.connector_id]) == null ? void 0 : D.length) ?? 0,
          " granted"
        ] })
      ] }),
      /* @__PURE__ */ s.jsx("p", { className: "muted-text", children: S.description }),
      /* @__PURE__ */ s.jsx("div", { className: "feature-list compact-list", children: S.operations.map((F) => /* @__PURE__ */ s.jsxs("label", { className: "session-tool-row", children: [
        /* @__PURE__ */ s.jsxs("span", { className: "session-tool-row-main", children: [
          /* @__PURE__ */ s.jsx(
            "input",
            {
              type: "checkbox",
              checked: (c[S.connector_id] ?? []).includes(F.name),
              onChange: (B) => P(S.connector_id, F.name, B.target.checked)
            }
          ),
          /* @__PURE__ */ s.jsxs("span", { children: [
            /* @__PURE__ */ s.jsx("strong", { children: F.name }),
            /* @__PURE__ */ s.jsxs("span", { className: "muted-text", children: [
              " ",
              F.description
            ] })
          ] })
        ] }),
        /* @__PURE__ */ s.jsxs("span", { className: "session-tool-row-meta", children: [
          /* @__PURE__ */ s.jsx("span", { className: "status-pill state-idle", children: F.access }),
          F.requires_approval ? /* @__PURE__ */ s.jsx("span", { className: "status-pill state-waiting", children: "Approval" }) : null
        ] })
      ] }, F.name)) })
    ] }, S.connector_id);
  }) }) : /* @__PURE__ */ s.jsx("div", { className: "empty-state", children: "No connectors are configured for this directory." });
}
function Vf(c, g, a) {
  const P = ((a == null ? void 0 : a.agent_providers) ?? []).some(
    (D) => D.value === c.agentProvider && D.execution_backends.includes(g)
  ), S = c.agentProvider && P ? c.agentProvider : "";
  return {
    ...c,
    backend: g,
    workerId: g === "container_worker_vm" ? c.workerId : "",
    mssql: g === "libvirt_vm" ? c.mssql : !1,
    mssqlInitRoot: g === "libvirt_vm" ? c.mssqlInitRoot : "",
    agentProvider: S,
    modelProviderMode: S && S !== "codex" ? "" : c.modelProviderMode,
    codexAuthMode: g === "container_worker_vm" && (!S || S === "codex") ? c.codexAuthMode : "",
    subscriptionProfileId: g === "container_worker_vm" && (!S || S === "codex") ? c.subscriptionProfileId : ""
  };
}
function $f(c) {
  const g = c.trim();
  return !g || oc(g) == null;
}
function Hf({
  directoryPath: c,
  onClose: g,
  onCreate: a,
  title: P = "Create agent",
  description: S = null,
  submitLabel: D = "Create",
  submittingLabel: F = "Queueing...",
  closeLabel: B = "Close",
  privilegedUnlocked: _ = !0,
  closeOnSuccess: M = !0,
  requireSessionId: k = !1,
  requireSessionIdMessage: L = "Session id is required."
}) {
  const [w, E] = te.useState(() => qa()), [R, Y] = te.useState(null), [$, ae] = te.useState(!0), [ge, _e] = te.useState(""), [X, J] = te.useState(""), [b, Z] = te.useState(null), [ee, xe] = te.useState(null), [fe, Oe] = te.useState(!1);
  te.useEffect(() => {
    Y(null), E((f) => ({
      ...f,
      agentProvider: "",
      vmImage: "",
      containerImage: "",
      workerId: "",
      subscriptionProfileId: "",
      initialPolicy: {
        ...f.initialPolicy,
        connectorGrants: {},
        hostDirectoryGrants: {},
        communicationTargetIds: [],
        emailMailboxGrants: []
      }
    }));
  }, [c]), te.useEffect(() => {
    let f = !1;
    return ae(!0), _e(""), (async () => {
      try {
        const W = await Nf(c, w.workerId);
        f || Y(W);
      } catch (W) {
        f || _e(W instanceof Error ? W.message : "Unable to load worker options.");
      } finally {
        f || ae(!1);
      }
    })(), () => {
      f = !0;
    };
  }, [c, w.workerId]);
  const Te = te.useMemo(
    () => ((R == null ? void 0 : R.agent_providers) ?? []).filter(
      (f) => f.execution_backends.includes(w.backend)
    ),
    [w.backend, R]
  ), je = !w.agentProvider || w.agentProvider === "codex", Ae = w.backend === "container_worker_vm" && (!w.agentProvider || w.agentProvider === "codex"), We = w.codexAuthMode || (R == null ? void 0 : R.container_worker.codex_auth_mode) || "metered_api", Le = w.modelProviderMode || (R == null ? void 0 : R.container_worker.model_provider_mode) || "openai_public", ue = Ae && We === "chatgpt_subscription", z = ue && Le === "openai_public", Q = (R == null ? void 0 : R.communication_targets) ?? [], O = (R == null ? void 0 : R.email_mailboxes) ?? [];
  async function m() {
    if ($ || ge) return;
    if (w.backend === "libvirt_vm" && !w.vmName.trim()) {
      J("VM name is required.");
      return;
    }
    if (k && !w.sessionId.trim()) {
      J(L);
      return;
    }
    if (w.backend === "container_worker_vm" && !w.sessionId.trim()) {
      J("Session id is required for container workers.");
      return;
    }
    if ($f(w.ttlDuration)) {
      J(
        "Session TTL must use seconds, minutes, hours, days, or weeks, for example 30m, 8h, 7d, or 2w."
      );
      return;
    }
    if (ue && Le !== "openai_public") {
      J("ChatGPT subscription profiles require the Public OpenAI model relay.");
      return;
    }
    if (ue && !w.subscriptionProfileId.trim()) {
      J("Select a ChatGPT subscription profile.");
      return;
    }
    if (b) {
      J(b);
      return;
    }
    if (ee) {
      J(ee);
      return;
    }
    const f = {
      ...w,
      codexAuthMode: ue && !w.codexAuthMode ? "chatgpt_subscription" : w.codexAuthMode,
      subscriptionProfileId: ue ? w.subscriptionProfileId : ""
    }, x = jf(f, c), W = Ef(f);
    Oe(!0), J("");
    try {
      await a(x, { form: f, targetLabel: W }), E(qa()), Z(null), xe(null), M && g();
    } catch (re) {
      J(re instanceof Error ? re.message : String(re));
    } finally {
      Oe(!1);
    }
  }
  return /* @__PURE__ */ s.jsx("div", { className: "modal-backdrop", role: "presentation", children: /* @__PURE__ */ s.jsxs("section", { className: "modal-card agent-crud-modal", role: "dialog", "aria-modal": "true", "aria-label": P, children: [
    /* @__PURE__ */ s.jsxs("div", { className: "modal-header", children: [
      /* @__PURE__ */ s.jsxs("div", { children: [
        /* @__PURE__ */ s.jsx("h2", { children: P }),
        S ? /* @__PURE__ */ s.jsx("p", { children: S }) : null
      ] }),
      /* @__PURE__ */ s.jsx("button", { className: "admin-action-button compact", type: "button", onClick: g, disabled: fe, children: B })
    ] }),
    /* @__PURE__ */ s.jsxs("div", { className: "modal-body", children: [
      X ? /* @__PURE__ */ s.jsx("div", { className: "callout error-callout", children: X }) : null,
      ge ? /* @__PURE__ */ s.jsx("div", { role: "alert", className: "callout error-callout", children: ge }) : null,
      /* @__PURE__ */ s.jsxs("div", { className: "config-field-grid", children: [
        /* @__PURE__ */ s.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ s.jsx(se, { tooltip: "Execution target for the agent. VM agent launches a libvirt VM; Container worker prepares a worker session that runs in a managed container.", children: "Backend" }),
          /* @__PURE__ */ s.jsx("span", { className: "select-field-control", children: /* @__PURE__ */ s.jsxs(
            "select",
            {
              value: w.backend,
              onChange: (f) => {
                const x = f.target.value;
                E((W) => Vf(W, x, R));
              },
              children: [
                /* @__PURE__ */ s.jsx("option", { value: "libvirt_vm", children: "VM agent" }),
                /* @__PURE__ */ s.jsx("option", { value: "container_worker_vm", children: "Container worker" })
              ]
            }
          ) })
        ] }),
        /* @__PURE__ */ s.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ s.jsx(se, { tooltip: "Agent CLI to bootstrap and run in the target. Claude bootstraps Claude Code for VM agents; Directory default uses the configured provider default.", children: "Agent provider" }),
          /* @__PURE__ */ s.jsx("span", { className: "select-field-control", children: /* @__PURE__ */ s.jsxs(
            "select",
            {
              value: w.agentProvider,
              onChange: (f) => E((x) => {
                const W = f.target.value;
                return {
                  ...x,
                  agentProvider: W,
                  modelProviderMode: W && W !== "codex" ? "" : x.modelProviderMode,
                  codexAuthMode: W && W !== "codex" ? "" : x.codexAuthMode,
                  subscriptionProfileId: W && W !== "codex" ? "" : x.subscriptionProfileId
                };
              }),
              children: [
                /* @__PURE__ */ s.jsx("option", { value: "", children: "Directory default" }),
                Te.map((f) => /* @__PURE__ */ s.jsx("option", { value: f.value, children: f.label }, f.value))
              ]
            }
          ) })
        ] }),
        je ? /* @__PURE__ */ s.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ s.jsx(se, { tooltip: "Relay mode for Codex model requests. Leave blank to use the directory default; choose Local OpenAI for host-local Ollama.", children: "Model relay" }),
          /* @__PURE__ */ s.jsx("span", { className: "select-field-control", children: /* @__PURE__ */ s.jsxs(
            "select",
            {
              value: w.modelProviderMode,
              onChange: (f) => E((x) => ({
                ...x,
                modelProviderMode: f.target.value
              })),
              children: [
                /* @__PURE__ */ s.jsx("option", { value: "", children: "Directory default" }),
                ((R == null ? void 0 : R.model_provider_modes) ?? []).map((f) => /* @__PURE__ */ s.jsx("option", { value: f.value, children: f.label }, f.value))
              ]
            }
          ) })
        ] }) : null,
        Ae ? /* @__PURE__ */ s.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ s.jsx(se, { tooltip: "How Codex authenticates on this container worker. Metered API uses the configured OpenAI relay. ChatGPT subscription uses the worker-owned signed-in account. The choice applies on the next turn and does not replace the Codex thread.", children: "Codex usage" }),
          /* @__PURE__ */ s.jsx("span", { className: "select-field-control", children: /* @__PURE__ */ s.jsxs(
            "select",
            {
              value: w.codexAuthMode,
              onChange: (f) => E((x) => ({
                ...x,
                codexAuthMode: f.target.value,
                subscriptionProfileId: f.target.value === "chatgpt_subscription" ? x.subscriptionProfileId : ""
              })),
              children: [
                /* @__PURE__ */ s.jsx("option", { value: "", children: "Directory default" }),
                /* @__PURE__ */ s.jsx("option", { value: "metered_api", children: "Metered API" }),
                /* @__PURE__ */ s.jsx("option", { value: "chatgpt_subscription", children: "ChatGPT subscription" })
              ]
            }
          ) })
        ] }) : null,
        z ? /* @__PURE__ */ s.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ s.jsx(se, { tooltip: "Worker-owned ChatGPT account binding. This selection is fixed for the lifetime of the agent session.", children: "Subscription profile" }),
          /* @__PURE__ */ s.jsx("span", { className: "select-field-control", children: /* @__PURE__ */ s.jsxs(
            "select",
            {
              value: w.subscriptionProfileId,
              disabled: $,
              onChange: (f) => E((x) => ({
                ...x,
                subscriptionProfileId: f.target.value
              })),
              children: [
                /* @__PURE__ */ s.jsx("option", { value: "", children: "Select a profile" }),
                ((R == null ? void 0 : R.container_worker.subscription_profiles) ?? []).map((f) => /* @__PURE__ */ s.jsxs("option", { value: f.profile_id, children: [
                  f.display_name,
                  " (",
                  f.profile_id,
                  ")"
                ] }, f.profile_id))
              ]
            }
          ) }),
          ((R == null ? void 0 : R.container_worker.subscription_profiles) ?? []).length === 0 ? /* @__PURE__ */ s.jsx("span", { className: "field-description", children: "No enabled profiles are available on this worker. Create or enable one on Subscriptions." }) : null
        ] }) : null,
        /* @__PURE__ */ s.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ s.jsx(se, { tooltip: "Host-side session id for policy, logs, model routing, and resume state. Required for container workers; VM agents can derive one from the VM name.", children: "Session" }),
          /* @__PURE__ */ s.jsx(
            "input",
            {
              value: w.sessionId,
              onChange: (f) => E((x) => ({ ...x, sessionId: f.target.value })),
              placeholder: k || w.backend === "container_worker_vm" ? "Required" : "Optional"
            }
          )
        ] }),
        /* @__PURE__ */ s.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ s.jsx(se, { tooltip: "How long the created session remains active before expiry. Use s, m, h, d, or w, for example 30m, 8h, 7d, or 2w.", children: "Session TTL" }),
          /* @__PURE__ */ s.jsx(
            "input",
            {
              value: w.ttlDuration,
              onChange: (f) => E((x) => ({ ...x, ttlDuration: f.target.value })),
              placeholder: "7d"
            }
          )
        ] }),
        /* @__PURE__ */ s.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ s.jsx(se, { tooltip: "Model stored on the session for runtime prompts and resumes. Leave blank to use the selected agent provider or directory default.", children: "Model" }),
          /* @__PURE__ */ s.jsx(
            "input",
            {
              value: w.model,
              onChange: (f) => E((x) => ({ ...x, model: f.target.value })),
              placeholder: w.agentProvider === "claude" ? "claude-sonnet-4-6" : "Directory default"
            }
          )
        ] }),
        w.backend === "libvirt_vm" ? /* @__PURE__ */ s.jsxs(s.Fragment, { children: [
          /* @__PURE__ */ s.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ s.jsx(se, { tooltip: "Libvirt domain name to create for this VM-backed agent. It must be unique on the host.", children: "VM name" }),
            /* @__PURE__ */ s.jsx(
              "input",
              {
                value: w.vmName,
                onChange: (f) => E((x) => ({ ...x, vmName: f.target.value }))
              }
            )
          ] }),
          /* @__PURE__ */ s.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ s.jsx(se, { tooltip: "VM infrastructure provider, not the model provider. Leave blank to use the directory default, usually libvirt.", children: "Provider" }),
            /* @__PURE__ */ s.jsx(
              "input",
              {
                value: w.provider,
                onChange: (f) => E((x) => ({ ...x, provider: f.target.value })),
                placeholder: "Directory default"
              }
            )
          ] }),
          /* @__PURE__ */ s.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ s.jsx(se, { tooltip: "Guest runtime user used for bootstraps and agent commands. Current standard VM images use agent.", children: "VM user" }),
            /* @__PURE__ */ s.jsx(
              "input",
              {
                value: w.vmUser,
                onChange: (f) => E((x) => ({ ...x, vmUser: f.target.value }))
              }
            )
          ] }),
          /* @__PURE__ */ s.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ s.jsx(se, { tooltip: "Approved libvirt template alias for the base qcow2 image. Leave blank to use the directory default.", children: "VM image" }),
            /* @__PURE__ */ s.jsx("span", { className: "select-field-control", children: /* @__PURE__ */ s.jsxs(
              "select",
              {
                value: w.vmImage,
                onChange: (f) => E((x) => ({ ...x, vmImage: f.target.value })),
                children: [
                  /* @__PURE__ */ s.jsx("option", { value: "", children: `Directory default (${(R == null ? void 0 : R.libvirt.default_template) ?? "default"})` }),
                  ((R == null ? void 0 : R.libvirt.templates) ?? []).map((f) => /* @__PURE__ */ s.jsx("option", { value: f.alias, children: f.alias }, f.alias))
                ]
              }
            ) })
          ] })
        ] }) : /* @__PURE__ */ s.jsxs(s.Fragment, { children: [
          /* @__PURE__ */ s.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ s.jsx(se, { tooltip: "Display and control-plane target name for this container-worker agent. Leave blank to reuse the session id.", children: "Target name" }),
            /* @__PURE__ */ s.jsx(
              "input",
              {
                value: w.targetName,
                onChange: (f) => E((x) => ({ ...x, targetName: f.target.value })),
                placeholder: "Optional"
              }
            )
          ] }),
          /* @__PURE__ */ s.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ s.jsx(se, { tooltip: "Worker for this new agent. Leave blank to use the directory default. The agent will keep its assigned worker for future work.", children: "Worker" }),
            /* @__PURE__ */ s.jsx("span", { className: "select-field-control", children: /* @__PURE__ */ s.jsxs(
              "select",
              {
                value: w.workerId,
                disabled: fe,
                onChange: (f) => {
                  ae(!0), E((x) => ({
                    ...x,
                    workerId: f.target.value,
                    containerImage: "",
                    subscriptionProfileId: ""
                  }));
                },
                children: [
                  /* @__PURE__ */ s.jsx("option", { value: "", children: "Directory default" }),
                  ((R == null ? void 0 : R.container_worker.workers) ?? []).map((f) => /* @__PURE__ */ s.jsx("option", { value: f.worker_id, children: f.display_name }, f.worker_id))
                ]
              }
            ) })
          ] }),
          /* @__PURE__ */ s.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ s.jsx(se, { tooltip: "Approved container image alias for worker sessions. Leave blank to use the selected worker's default runner image.", children: "Container image selector" }),
            R ? /* @__PURE__ */ s.jsx("span", { className: "select-field-control", children: /* @__PURE__ */ s.jsxs(
              "select",
              {
                value: w.containerImage,
                disabled: $,
                onChange: (f) => E((x) => ({ ...x, containerImage: f.target.value })),
                children: [
                  /* @__PURE__ */ s.jsx("option", { value: "", children: `Default image (${R.container_worker.default_image ?? "default"})` }),
                  R.container_worker.images.map((f) => /* @__PURE__ */ s.jsx("option", { value: f.alias, children: f.alias }, f.alias))
                ]
              }
            ) }) : /* @__PURE__ */ s.jsx(
              "input",
              {
                value: w.containerImage,
                onChange: (f) => E((x) => ({ ...x, containerImage: f.target.value })),
                placeholder: "Alias or default image"
              }
            )
          ] })
        ] }),
        /* @__PURE__ */ s.jsxs("fieldset", { className: "agent-capability-group", children: [
          /* @__PURE__ */ s.jsx("legend", { children: /* @__PURE__ */ s.jsx(se, { tooltip: "Optional capabilities to enable when the agent is created.", children: "Capabilities" }) }),
          /* @__PURE__ */ s.jsxs("div", { className: "agent-capability-grid", children: [
            /* @__PURE__ */ s.jsxs("label", { className: "checkbox-row agent-capability-option", children: [
              /* @__PURE__ */ s.jsx(
                "input",
                {
                  type: "checkbox",
                  checked: w.dotnet,
                  onChange: (f) => E((x) => ({ ...x, dotnet: f.target.checked }))
                }
              ),
              /* @__PURE__ */ s.jsx(se, { tooltip: "Seed .NET support for the session. VM agents copy the host SDK; container workers point at the seeded worker cache mount.", children: ".NET" })
            ] }),
            /* @__PURE__ */ s.jsxs("label", { className: "checkbox-row agent-capability-option", children: [
              /* @__PURE__ */ s.jsx(
                "input",
                {
                  type: "checkbox",
                  checked: w.nuget,
                  onChange: (f) => E((x) => ({ ...x, nuget: f.target.checked }))
                }
              ),
              /* @__PURE__ */ s.jsx(se, { tooltip: "Configure package restore through the host NuGet relay and session cache.", children: "NuGet" })
            ] }),
            w.backend === "libvirt_vm" ? /* @__PURE__ */ s.jsxs("label", { className: "checkbox-row agent-capability-option", children: [
              /* @__PURE__ */ s.jsx(
                "input",
                {
                  type: "checkbox",
                  checked: w.mssql,
                  onChange: (f) => E((x) => ({ ...x, mssql: f.target.checked }))
                }
              ),
              /* @__PURE__ */ s.jsx(se, { tooltip: "Provision a guest-local SQL Server instance for VM agents. Container worker SQL uses session-owned MCP provisioning instead.", children: "MSSQL" })
            ] }) : null,
            /* @__PURE__ */ s.jsxs("label", { className: "checkbox-row agent-capability-option", children: [
              /* @__PURE__ */ s.jsx(
                "input",
                {
                  type: "checkbox",
                  checked: w.playwright,
                  onChange: (f) => E((x) => ({ ...x, playwright: f.target.checked }))
                }
              ),
              /* @__PURE__ */ s.jsx(se, { tooltip: "Seed Playwright support for the session. VM agents install browser dependencies; container workers point at the seeded browser cache.", children: "Playwright" })
            ] }),
            /* @__PURE__ */ s.jsxs("label", { className: "checkbox-row agent-capability-option", children: [
              /* @__PURE__ */ s.jsx(
                "input",
                {
                  type: "checkbox",
                  checked: w.initialPolicy.webSearchEnabled,
                  onChange: (f) => E((x) => ({
                    ...x,
                    initialPolicy: { ...x.initialPolicy, webSearchEnabled: f.target.checked }
                  }))
                }
              ),
              /* @__PURE__ */ s.jsx(se, { tooltip: "Allow this session's agent runner to use live web search when the selected provider supports it.", children: "Web search" })
            ] })
          ] }),
          w.backend === "libvirt_vm" && w.mssql ? /* @__PURE__ */ s.jsx("div", { className: "agent-capability-detail-row", children: /* @__PURE__ */ s.jsxs("label", { className: "field-stack agent-capability-mssql-root", children: [
            /* @__PURE__ */ s.jsx(se, { tooltip: "Optional host directory copied into the SQL init folder. If init.sql exists there, it runs after SQL Server starts.", children: "MSSQL init root" }),
            /* @__PURE__ */ s.jsx(
              "input",
              {
                value: w.mssqlInitRoot,
                onChange: (f) => E((x) => ({ ...x, mssqlInitRoot: f.target.value }))
              }
            )
          ] }) }) : null
        ] }),
        /* @__PURE__ */ s.jsxs("div", { className: "field-stack wide-field", children: [
          /* @__PURE__ */ s.jsx(se, { tooltip: "Session policy values written when the agent is created. Empty rows are ignored.", children: "Initial policy" }),
          /* @__PURE__ */ s.jsx("p", { className: "field-description", children: "Optional session policy values to apply when the agent is created. Blank rows are not sent." })
        ] }),
        /* @__PURE__ */ s.jsxs("div", { className: "field-stack", children: [
          /* @__PURE__ */ s.jsx(se, { tooltip: "Key/value metadata stored on the session and shown in agent lists.", children: "Labels" }),
          /* @__PURE__ */ s.jsx(
            yf,
            {
              value: w.initialPolicy.labels,
              onChange: (f) => E((x) => ({
                ...x,
                initialPolicy: { ...x.initialPolicy, labels: f }
              })),
              onValidationChange: Z,
              fieldLabel: "Labels",
              emptyState: "No labels are configured.",
              addLabel: "Add label",
              keyLabel: "Key",
              valueLabel: "Value"
            }
          )
        ] }),
        /* @__PURE__ */ s.jsxs("div", { className: "field-stack wide-field", children: [
          /* @__PURE__ */ s.jsx(se, { tooltip: "Capabilities granted for named host directories. Selecting one also enables its dedicated MCP tool; delete normally requires approval and moves entries to quarantine.", children: "Host directory grants" }),
          /* @__PURE__ */ s.jsx(
            Ff,
            {
              values: w.initialPolicy.hostDirectoryGrants,
              options: (R == null ? void 0 : R.host_directories) ?? [],
              onChange: (f) => E((x) => ({
                ...x,
                initialPolicy: {
                  ...x.initialPolicy,
                  hostDirectoryGrants: f,
                  allowedTools: cn([
                    ...x.initialPolicy.allowedTools,
                    ...Af(f)
                  ])
                }
              }))
            }
          )
        ] }),
        /* @__PURE__ */ s.jsxs("div", { className: "field-stack", children: [
          /* @__PURE__ */ s.jsx(se, { tooltip: "MCP tools this session may call without being hidden by policy. Approval rules still apply.", children: "Allowed tools" }),
          /* @__PURE__ */ s.jsx(
            Ka,
            {
              values: w.initialPolicy.allowedTools,
              onChange: (f) => E((x) => ({
                ...x,
                initialPolicy: { ...x.initialPolicy, allowedTools: f }
              })),
              addLabel: "Add tool",
              itemLabel: "Tool",
              placeholder: "run_script",
              emptyState: "No tools are allowed explicitly."
            }
          )
        ] }),
        /* @__PURE__ */ s.jsxs("div", { className: "field-stack wide-field", children: [
          /* @__PURE__ */ s.jsx(se, { tooltip: "Named connector operations granted to the session. Selecting an operation also enables the generic connector discovery and call MCP tools.", children: "Connector grants" }),
          /* @__PURE__ */ s.jsx(
            Uf,
            {
              values: w.initialPolicy.connectorGrants,
              options: (R == null ? void 0 : R.connectors) ?? [],
              onChange: (f) => E((x) => ({
                ...x,
                initialPolicy: {
                  ...x.initialPolicy,
                  connectorGrants: f,
                  allowedTools: Object.keys(f).length ? cn([...x.initialPolicy.allowedTools, "list_connectors", "call_connector"]) : x.initialPolicy.allowedTools
                }
              }))
            }
          )
        ] }),
        /* @__PURE__ */ s.jsxs("div", { className: "field-stack", children: [
          /* @__PURE__ */ s.jsx(se, { tooltip: "Script names this session may run through approved script execution paths.", children: "Allowed scripts" }),
          /* @__PURE__ */ s.jsx(
            Ka,
            {
              values: w.initialPolicy.allowedScriptNames,
              onChange: (f) => E((x) => ({
                ...x,
                initialPolicy: { ...x.initialPolicy, allowedScriptNames: f }
              })),
              addLabel: "Add script",
              itemLabel: "Script",
              placeholder: "collect-logs",
              emptyState: "No scripts are allowed explicitly."
            }
          )
        ] }),
        /* @__PURE__ */ s.jsxs("div", { className: "field-stack wide-field", children: [
          /* @__PURE__ */ s.jsx(se, { tooltip: "Named HTTP(S) base URLs this session may reach through the host HTTP target relay. Optionally grant a managed website login profile for host-owned authentication.", children: "HTTP targets" }),
          /* @__PURE__ */ s.jsx(
            Df,
            {
              directoryPath: c,
              entries: w.initialPolicy.httpTargets,
              onChange: (f) => E((x) => ({
                ...x,
                initialPolicy: { ...x.initialPolicy, httpTargets: f }
              })),
              onValidationChange: xe,
              sessionId: w.sessionId.trim() || null
            }
          )
        ] }),
        /* @__PURE__ */ s.jsxs("div", { className: "field-stack wide-field agent-communication-targets-field", children: [
          /* @__PURE__ */ s.jsx(se, { tooltip: "Directory communication targets the session may use for messaging. Only registered targets with session-compatible capabilities are selectable.", children: "Communication targets" }),
          /* @__PURE__ */ s.jsx(
            zf,
            {
              values: w.initialPolicy.communicationTargetIds,
              options: Q,
              onChange: (f) => E((x) => ({
                ...x,
                initialPolicy: { ...x.initialPolicy, communicationTargetIds: f }
              })),
              emptyState: Q.length ? "No communication targets selected." : "No session-capable communication targets are registered for this directory."
            }
          )
        ] }),
        /* @__PURE__ */ s.jsxs("div", { className: "field-stack wide-field agent-communication-targets-field", children: [
          /* @__PURE__ */ s.jsx(se, { tooltip: "Mailbox account access is separate from outbound destinations. Read-only is the default; draft and send access must be granted explicitly.", children: "Email mailbox access" }),
          /* @__PURE__ */ s.jsx(
            If,
            {
              grants: w.initialPolicy.emailMailboxGrants,
              options: O,
              onChange: (f) => E((x) => ({
                ...x,
                initialPolicy: { ...x.initialPolicy, emailMailboxGrants: f }
              })),
              emptyState: O.length ? "No mailbox accounts selected." : "No mailbox accounts are configured for this directory."
            }
          )
        ] })
      ] }),
      /* @__PURE__ */ s.jsx("div", { className: "inline-actions compact-actions", children: /* @__PURE__ */ s.jsx("button", { className: "admin-action-button primary compact", type: "button", disabled: !_ || fe || $ || !!ge, onClick: () => void m(), children: fe ? F : D }) })
    ] })
  ] }) });
}
const Bf = 1500, Wf = 600 * 1e3, ba = "Session id is required so the created core session can be registered in Team.";
function Qf(c) {
  return new Promise((g) => window.setTimeout(g, c));
}
async function Gf(c) {
  let g = c;
  const a = Date.now() + Wf;
  for (; Lf(g); ) {
    if (Date.now() >= a)
      throw new Error(`Timed out waiting for ${g.target_name} to finish creating.`);
    await Qf(Bf), g = await Tf(g.operation_id);
  }
  if (g.status === "failed")
    throw new Error(g.error || `Agent create failed for ${g.target_name}.`);
  return g;
}
function Kf(c) {
  window.dispatchEvent(new CustomEvent("team-agent-create:created", { detail: c }));
}
function Yf() {
  return new URLSearchParams(window.location.search).get("privileged_unlocked") === "true";
}
function qf() {
  var B;
  const [c, g] = te.useState(null), [a, P] = te.useState(""), [S, D] = te.useState(Yf);
  if (te.useEffect(() => {
    const _ = (M) => {
      var L;
      if (M.origin !== window.location.origin)
        return;
      const k = M.data || {};
      k.type === "admin-privileged-operations" && D(!!((L = k.privileged_operations) != null && L.unlocked));
    };
    return window.addEventListener("message", _), () => {
      window.removeEventListener("message", _);
    };
  }, []), te.useEffect(() => {
    const _ = (k) => {
      P(""), g({
        contextId: (k == null ? void 0 : k.contextId) ?? null,
        directoryPath: (k == null ? void 0 : k.directoryPath) ?? "root"
      });
    };
    window.openTeamAgentCreateDialog = _;
    const M = (k) => {
      _(k.detail ?? {});
    };
    return window.addEventListener("team-agent-create:open", M), () => {
      window.openTeamAgentCreateDialog === _ && delete window.openTeamAgentCreateDialog, window.removeEventListener("team-agent-create:open", M);
    };
  }, []), !c)
    return null;
  async function F(_) {
    var w;
    if (!S)
      throw new Error("Unlock privileged operations to create a core agent session.");
    if (!((w = _.session_id) != null && w.trim()))
      throw new Error(ba);
    P("Queueing core agent session.");
    const M = await Pf(_);
    P(`Creating ${M.target_name}.`);
    const k = await Gf(M), L = Rf(k);
    if (!L)
      throw new Error(`Created ${k.target_name}, but the create operation did not return a session id.`);
    P("Registering Team agent."), Kf({
      contextId: (c == null ? void 0 : c.contextId) ?? null,
      sessionId: L,
      targetName: k.target_name,
      operation: k
    });
  }
  return /* @__PURE__ */ s.jsx(
    Hf,
    {
      directoryPath: ((B = c.directoryPath) == null ? void 0 : B.trim()) || "root",
      title: "Create and register agent",
      description: a || "Create a VM or container session, then Team registration will continue automatically.",
      submitLabel: "Create and register",
      submittingLabel: "Creating...",
      privilegedUnlocked: S,
      requireSessionId: !0,
      requireSessionIdMessage: ba,
      closeOnSuccess: !0,
      onClose: () => {
        g(null), P("");
      },
      onCreate: F
    }
  );
}
function Xf() {
  let c = document.querySelector("#team-agent-create-root");
  return c || (c = document.createElement("div"), c.id = "team-agent-create-root", document.body.appendChild(c)), c;
}
let Ao = null;
function ec() {
  const c = Xf();
  Ao || (Ao = pf.createRoot(c)), Ao.render(/* @__PURE__ */ s.jsx(qf, {}));
}
document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", ec, { once: !0 }) : ec();
