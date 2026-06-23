function hd(e) {
  return e && e.__esModule && Object.prototype.hasOwnProperty.call(e, "default") ? e.default : e;
}
var pd = { exports: {} }, fs = {}, gd = { exports: {} }, te = {};
/**
 * @license React
 * react.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var zo = Symbol.for("react.element"), x0 = Symbol.for("react.portal"), S0 = Symbol.for("react.fragment"), _0 = Symbol.for("react.strict_mode"), E0 = Symbol.for("react.profiler"), k0 = Symbol.for("react.provider"), N0 = Symbol.for("react.context"), C0 = Symbol.for("react.forward_ref"), M0 = Symbol.for("react.suspense"), P0 = Symbol.for("react.memo"), z0 = Symbol.for("react.lazy"), ec = Symbol.iterator;
function I0(e) {
  return e === null || typeof e != "object" ? null : (e = ec && e[ec] || e["@@iterator"], typeof e == "function" ? e : null);
}
var md = { isMounted: function() {
  return !1;
}, enqueueForceUpdate: function() {
}, enqueueReplaceState: function() {
}, enqueueSetState: function() {
} }, yd = Object.assign, vd = {};
function Sr(e, t, n) {
  this.props = e, this.context = t, this.refs = vd, this.updater = n || md;
}
Sr.prototype.isReactComponent = {};
Sr.prototype.setState = function(e, t) {
  if (typeof e != "object" && typeof e != "function" && e != null) throw Error("setState(...): takes an object of state variables to update or a function which returns an object of state variables.");
  this.updater.enqueueSetState(this, e, t, "setState");
};
Sr.prototype.forceUpdate = function(e) {
  this.updater.enqueueForceUpdate(this, e, "forceUpdate");
};
function wd() {
}
wd.prototype = Sr.prototype;
function $u(e, t, n) {
  this.props = e, this.context = t, this.refs = vd, this.updater = n || md;
}
var Du = $u.prototype = new wd();
Du.constructor = $u;
yd(Du, Sr.prototype);
Du.isPureReactComponent = !0;
var tc = Array.isArray, xd = Object.prototype.hasOwnProperty, Ru = { current: null }, Sd = { key: !0, ref: !0, __self: !0, __source: !0 };
function _d(e, t, n) {
  var r, o = {}, i = null, s = null;
  if (t != null) for (r in t.ref !== void 0 && (s = t.ref), t.key !== void 0 && (i = "" + t.key), t) xd.call(t, r) && !Sd.hasOwnProperty(r) && (o[r] = t[r]);
  var l = arguments.length - 2;
  if (l === 1) o.children = n;
  else if (1 < l) {
    for (var u = Array(l), a = 0; a < l; a++) u[a] = arguments[a + 2];
    o.children = u;
  }
  if (e && e.defaultProps) for (r in l = e.defaultProps, l) o[r] === void 0 && (o[r] = l[r]);
  return { $$typeof: zo, type: e, key: i, ref: s, props: o, _owner: Ru.current };
}
function T0(e, t) {
  return { $$typeof: zo, type: e.type, key: t, ref: e.ref, props: e.props, _owner: e._owner };
}
function ju(e) {
  return typeof e == "object" && e !== null && e.$$typeof === zo;
}
function L0(e) {
  var t = { "=": "=0", ":": "=2" };
  return "$" + e.replace(/[=:]/g, function(n) {
    return t[n];
  });
}
var nc = /\/+/g;
function Ws(e, t) {
  return typeof e == "object" && e !== null && e.key != null ? L0("" + e.key) : t.toString(36);
}
function pi(e, t, n, r, o) {
  var i = typeof e;
  (i === "undefined" || i === "boolean") && (e = null);
  var s = !1;
  if (e === null) s = !0;
  else switch (i) {
    case "string":
    case "number":
      s = !0;
      break;
    case "object":
      switch (e.$$typeof) {
        case zo:
        case x0:
          s = !0;
      }
  }
  if (s) return s = e, o = o(s), e = r === "" ? "." + Ws(s, 0) : r, tc(o) ? (n = "", e != null && (n = e.replace(nc, "$&/") + "/"), pi(o, t, n, "", function(a) {
    return a;
  })) : o != null && (ju(o) && (o = T0(o, n + (!o.key || s && s.key === o.key ? "" : ("" + o.key).replace(nc, "$&/") + "/") + e)), t.push(o)), 1;
  if (s = 0, r = r === "" ? "." : r + ":", tc(e)) for (var l = 0; l < e.length; l++) {
    i = e[l];
    var u = r + Ws(i, l);
    s += pi(i, t, n, u, o);
  }
  else if (u = I0(e), typeof u == "function") for (e = u.call(e), l = 0; !(i = e.next()).done; ) i = i.value, u = r + Ws(i, l++), s += pi(i, t, n, u, o);
  else if (i === "object") throw t = String(e), Error("Objects are not valid as a React child (found: " + (t === "[object Object]" ? "object with keys {" + Object.keys(e).join(", ") + "}" : t) + "). If you meant to render a collection of children, use an array instead.");
  return s;
}
function Bo(e, t, n) {
  if (e == null) return e;
  var r = [], o = 0;
  return pi(e, r, "", "", function(i) {
    return t.call(n, i, o++);
  }), r;
}
function A0(e) {
  if (e._status === -1) {
    var t = e._result;
    t = t(), t.then(function(n) {
      (e._status === 0 || e._status === -1) && (e._status = 1, e._result = n);
    }, function(n) {
      (e._status === 0 || e._status === -1) && (e._status = 2, e._result = n);
    }), e._status === -1 && (e._status = 0, e._result = t);
  }
  if (e._status === 1) return e._result.default;
  throw e._result;
}
var De = { current: null }, gi = { transition: null }, $0 = { ReactCurrentDispatcher: De, ReactCurrentBatchConfig: gi, ReactCurrentOwner: Ru };
function Ed() {
  throw Error("act(...) is not supported in production builds of React.");
}
te.Children = { map: Bo, forEach: function(e, t, n) {
  Bo(e, function() {
    t.apply(this, arguments);
  }, n);
}, count: function(e) {
  var t = 0;
  return Bo(e, function() {
    t++;
  }), t;
}, toArray: function(e) {
  return Bo(e, function(t) {
    return t;
  }) || [];
}, only: function(e) {
  if (!ju(e)) throw Error("React.Children.only expected to receive a single React element child.");
  return e;
} };
te.Component = Sr;
te.Fragment = S0;
te.Profiler = E0;
te.PureComponent = $u;
te.StrictMode = _0;
te.Suspense = M0;
te.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED = $0;
te.act = Ed;
te.cloneElement = function(e, t, n) {
  if (e == null) throw Error("React.cloneElement(...): The argument must be a React element, but you passed " + e + ".");
  var r = yd({}, e.props), o = e.key, i = e.ref, s = e._owner;
  if (t != null) {
    if (t.ref !== void 0 && (i = t.ref, s = Ru.current), t.key !== void 0 && (o = "" + t.key), e.type && e.type.defaultProps) var l = e.type.defaultProps;
    for (u in t) xd.call(t, u) && !Sd.hasOwnProperty(u) && (r[u] = t[u] === void 0 && l !== void 0 ? l[u] : t[u]);
  }
  var u = arguments.length - 2;
  if (u === 1) r.children = n;
  else if (1 < u) {
    l = Array(u);
    for (var a = 0; a < u; a++) l[a] = arguments[a + 2];
    r.children = l;
  }
  return { $$typeof: zo, type: e.type, key: o, ref: i, props: r, _owner: s };
};
te.createContext = function(e) {
  return e = { $$typeof: N0, _currentValue: e, _currentValue2: e, _threadCount: 0, Provider: null, Consumer: null, _defaultValue: null, _globalName: null }, e.Provider = { $$typeof: k0, _context: e }, e.Consumer = e;
};
te.createElement = _d;
te.createFactory = function(e) {
  var t = _d.bind(null, e);
  return t.type = e, t;
};
te.createRef = function() {
  return { current: null };
};
te.forwardRef = function(e) {
  return { $$typeof: C0, render: e };
};
te.isValidElement = ju;
te.lazy = function(e) {
  return { $$typeof: z0, _payload: { _status: -1, _result: e }, _init: A0 };
};
te.memo = function(e, t) {
  return { $$typeof: P0, type: e, compare: t === void 0 ? null : t };
};
te.startTransition = function(e) {
  var t = gi.transition;
  gi.transition = {};
  try {
    e();
  } finally {
    gi.transition = t;
  }
};
te.unstable_act = Ed;
te.useCallback = function(e, t) {
  return De.current.useCallback(e, t);
};
te.useContext = function(e) {
  return De.current.useContext(e);
};
te.useDebugValue = function() {
};
te.useDeferredValue = function(e) {
  return De.current.useDeferredValue(e);
};
te.useEffect = function(e, t) {
  return De.current.useEffect(e, t);
};
te.useId = function() {
  return De.current.useId();
};
te.useImperativeHandle = function(e, t, n) {
  return De.current.useImperativeHandle(e, t, n);
};
te.useInsertionEffect = function(e, t) {
  return De.current.useInsertionEffect(e, t);
};
te.useLayoutEffect = function(e, t) {
  return De.current.useLayoutEffect(e, t);
};
te.useMemo = function(e, t) {
  return De.current.useMemo(e, t);
};
te.useReducer = function(e, t, n) {
  return De.current.useReducer(e, t, n);
};
te.useRef = function(e) {
  return De.current.useRef(e);
};
te.useState = function(e) {
  return De.current.useState(e);
};
te.useSyncExternalStore = function(e, t, n) {
  return De.current.useSyncExternalStore(e, t, n);
};
te.useTransition = function() {
  return De.current.useTransition();
};
te.version = "18.3.1";
gd.exports = te;
var F = gd.exports;
const D0 = /* @__PURE__ */ hd(F);
/**
 * @license React
 * react-jsx-runtime.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var R0 = F, j0 = Symbol.for("react.element"), O0 = Symbol.for("react.fragment"), F0 = Object.prototype.hasOwnProperty, H0 = R0.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED.ReactCurrentOwner, V0 = { key: !0, ref: !0, __self: !0, __source: !0 };
function kd(e, t, n) {
  var r, o = {}, i = null, s = null;
  n !== void 0 && (i = "" + n), t.key !== void 0 && (i = "" + t.key), t.ref !== void 0 && (s = t.ref);
  for (r in t) F0.call(t, r) && !V0.hasOwnProperty(r) && (o[r] = t[r]);
  if (e && e.defaultProps) for (r in t = e.defaultProps, t) o[r] === void 0 && (o[r] = t[r]);
  return { $$typeof: j0, type: e, key: i, ref: s, props: o, _owner: H0.current };
}
fs.Fragment = O0;
fs.jsx = kd;
fs.jsxs = kd;
pd.exports = fs;
var _ = pd.exports, Nd = { exports: {} }, Ke = {}, Cd = { exports: {} }, Md = {};
/**
 * @license React
 * scheduler.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
(function(e) {
  function t(N, C) {
    var L = N.length;
    N.push(C);
    e: for (; 0 < L; ) {
      var O = L - 1 >>> 1, R = N[O];
      if (0 < o(R, C)) N[O] = C, N[L] = R, L = O;
      else break e;
    }
  }
  function n(N) {
    return N.length === 0 ? null : N[0];
  }
  function r(N) {
    if (N.length === 0) return null;
    var C = N[0], L = N.pop();
    if (L !== C) {
      N[0] = L;
      e: for (var O = 0, R = N.length, W = R >>> 1; O < W; ) {
        var U = 2 * (O + 1) - 1, X = N[U], Q = U + 1, K = N[Q];
        if (0 > o(X, L)) Q < R && 0 > o(K, X) ? (N[O] = K, N[Q] = L, O = Q) : (N[O] = X, N[U] = L, O = U);
        else if (Q < R && 0 > o(K, L)) N[O] = K, N[Q] = L, O = Q;
        else break e;
      }
    }
    return C;
  }
  function o(N, C) {
    var L = N.sortIndex - C.sortIndex;
    return L !== 0 ? L : N.id - C.id;
  }
  if (typeof performance == "object" && typeof performance.now == "function") {
    var i = performance;
    e.unstable_now = function() {
      return i.now();
    };
  } else {
    var s = Date, l = s.now();
    e.unstable_now = function() {
      return s.now() - l;
    };
  }
  var u = [], a = [], d = 1, c = null, f = 3, m = !1, y = !1, w = !1, x = typeof setTimeout == "function" ? setTimeout : null, p = typeof clearTimeout == "function" ? clearTimeout : null, g = typeof setImmediate < "u" ? setImmediate : null;
  typeof navigator < "u" && navigator.scheduling !== void 0 && navigator.scheduling.isInputPending !== void 0 && navigator.scheduling.isInputPending.bind(navigator.scheduling);
  function h(N) {
    for (var C = n(a); C !== null; ) {
      if (C.callback === null) r(a);
      else if (C.startTime <= N) r(a), C.sortIndex = C.expirationTime, t(u, C);
      else break;
      C = n(a);
    }
  }
  function v(N) {
    if (w = !1, h(N), !y) if (n(u) !== null) y = !0, z(S);
    else {
      var C = n(a);
      C !== null && $(v, C.startTime - N);
    }
  }
  function S(N, C) {
    y = !1, w && (w = !1, p(P), P = -1), m = !0;
    var L = f;
    try {
      for (h(C), c = n(u); c !== null && (!(c.expirationTime > C) || N && !I()); ) {
        var O = c.callback;
        if (typeof O == "function") {
          c.callback = null, f = c.priorityLevel;
          var R = O(c.expirationTime <= C);
          C = e.unstable_now(), typeof R == "function" ? c.callback = R : c === n(u) && r(u), h(C);
        } else r(u);
        c = n(u);
      }
      if (c !== null) var W = !0;
      else {
        var U = n(a);
        U !== null && $(v, U.startTime - C), W = !1;
      }
      return W;
    } finally {
      c = null, f = L, m = !1;
    }
  }
  var E = !1, M = null, P = -1, T = 5, j = -1;
  function I() {
    return !(e.unstable_now() - j < T);
  }
  function D() {
    if (M !== null) {
      var N = e.unstable_now();
      j = N;
      var C = !0;
      try {
        C = M(!0, N);
      } finally {
        C ? H() : (E = !1, M = null);
      }
    } else E = !1;
  }
  var H;
  if (typeof g == "function") H = function() {
    g(D);
  };
  else if (typeof MessageChannel < "u") {
    var k = new MessageChannel(), A = k.port2;
    k.port1.onmessage = D, H = function() {
      A.postMessage(null);
    };
  } else H = function() {
    x(D, 0);
  };
  function z(N) {
    M = N, E || (E = !0, H());
  }
  function $(N, C) {
    P = x(function() {
      N(e.unstable_now());
    }, C);
  }
  e.unstable_IdlePriority = 5, e.unstable_ImmediatePriority = 1, e.unstable_LowPriority = 4, e.unstable_NormalPriority = 3, e.unstable_Profiling = null, e.unstable_UserBlockingPriority = 2, e.unstable_cancelCallback = function(N) {
    N.callback = null;
  }, e.unstable_continueExecution = function() {
    y || m || (y = !0, z(S));
  }, e.unstable_forceFrameRate = function(N) {
    0 > N || 125 < N ? console.error("forceFrameRate takes a positive int between 0 and 125, forcing frame rates higher than 125 fps is not supported") : T = 0 < N ? Math.floor(1e3 / N) : 5;
  }, e.unstable_getCurrentPriorityLevel = function() {
    return f;
  }, e.unstable_getFirstCallbackNode = function() {
    return n(u);
  }, e.unstable_next = function(N) {
    switch (f) {
      case 1:
      case 2:
      case 3:
        var C = 3;
        break;
      default:
        C = f;
    }
    var L = f;
    f = C;
    try {
      return N();
    } finally {
      f = L;
    }
  }, e.unstable_pauseExecution = function() {
  }, e.unstable_requestPaint = function() {
  }, e.unstable_runWithPriority = function(N, C) {
    switch (N) {
      case 1:
      case 2:
      case 3:
      case 4:
      case 5:
        break;
      default:
        N = 3;
    }
    var L = f;
    f = N;
    try {
      return C();
    } finally {
      f = L;
    }
  }, e.unstable_scheduleCallback = function(N, C, L) {
    var O = e.unstable_now();
    switch (typeof L == "object" && L !== null ? (L = L.delay, L = typeof L == "number" && 0 < L ? O + L : O) : L = O, N) {
      case 1:
        var R = -1;
        break;
      case 2:
        R = 250;
        break;
      case 5:
        R = 1073741823;
        break;
      case 4:
        R = 1e4;
        break;
      default:
        R = 5e3;
    }
    return R = L + R, N = { id: d++, callback: C, priorityLevel: N, startTime: L, expirationTime: R, sortIndex: -1 }, L > O ? (N.sortIndex = L, t(a, N), n(u) === null && N === n(a) && (w ? (p(P), P = -1) : w = !0, $(v, L - O))) : (N.sortIndex = R, t(u, N), y || m || (y = !0, z(S))), N;
  }, e.unstable_shouldYield = I, e.unstable_wrapCallback = function(N) {
    var C = f;
    return function() {
      var L = f;
      f = C;
      try {
        return N.apply(this, arguments);
      } finally {
        f = L;
      }
    };
  };
})(Md);
Cd.exports = Md;
var B0 = Cd.exports;
/**
 * @license React
 * react-dom.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var U0 = F, Qe = B0;
function V(e) {
  for (var t = "https://reactjs.org/docs/error-decoder.html?invariant=" + e, n = 1; n < arguments.length; n++) t += "&args[]=" + encodeURIComponent(arguments[n]);
  return "Minified React error #" + e + "; visit " + t + " for the full message or use the non-minified dev environment for full errors and additional helpful warnings.";
}
var Pd = /* @__PURE__ */ new Set(), to = {};
function Ln(e, t) {
  ir(e, t), ir(e + "Capture", t);
}
function ir(e, t) {
  for (to[e] = t, e = 0; e < t.length; e++) Pd.add(t[e]);
}
var Lt = !(typeof window > "u" || typeof window.document > "u" || typeof window.document.createElement > "u"), Cl = Object.prototype.hasOwnProperty, W0 = /^[:A-Z_a-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF\u0370-\u037D\u037F-\u1FFF\u200C-\u200D\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD][:A-Z_a-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF\u0370-\u037D\u037F-\u1FFF\u200C-\u200D\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD\-.0-9\u00B7\u0300-\u036F\u203F-\u2040]*$/, rc = {}, oc = {};
function Y0(e) {
  return Cl.call(oc, e) ? !0 : Cl.call(rc, e) ? !1 : W0.test(e) ? oc[e] = !0 : (rc[e] = !0, !1);
}
function X0(e, t, n, r) {
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
function Q0(e, t, n, r) {
  if (t === null || typeof t > "u" || X0(e, t, n, r)) return !0;
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
function Re(e, t, n, r, o, i, s) {
  this.acceptsBooleans = t === 2 || t === 3 || t === 4, this.attributeName = r, this.attributeNamespace = o, this.mustUseProperty = n, this.propertyName = e, this.type = t, this.sanitizeURL = i, this.removeEmptyString = s;
}
var Me = {};
"children dangerouslySetInnerHTML defaultValue defaultChecked innerHTML suppressContentEditableWarning suppressHydrationWarning style".split(" ").forEach(function(e) {
  Me[e] = new Re(e, 0, !1, e, null, !1, !1);
});
[["acceptCharset", "accept-charset"], ["className", "class"], ["htmlFor", "for"], ["httpEquiv", "http-equiv"]].forEach(function(e) {
  var t = e[0];
  Me[t] = new Re(t, 1, !1, e[1], null, !1, !1);
});
["contentEditable", "draggable", "spellCheck", "value"].forEach(function(e) {
  Me[e] = new Re(e, 2, !1, e.toLowerCase(), null, !1, !1);
});
["autoReverse", "externalResourcesRequired", "focusable", "preserveAlpha"].forEach(function(e) {
  Me[e] = new Re(e, 2, !1, e, null, !1, !1);
});
"allowFullScreen async autoFocus autoPlay controls default defer disabled disablePictureInPicture disableRemotePlayback formNoValidate hidden loop noModule noValidate open playsInline readOnly required reversed scoped seamless itemScope".split(" ").forEach(function(e) {
  Me[e] = new Re(e, 3, !1, e.toLowerCase(), null, !1, !1);
});
["checked", "multiple", "muted", "selected"].forEach(function(e) {
  Me[e] = new Re(e, 3, !0, e, null, !1, !1);
});
["capture", "download"].forEach(function(e) {
  Me[e] = new Re(e, 4, !1, e, null, !1, !1);
});
["cols", "rows", "size", "span"].forEach(function(e) {
  Me[e] = new Re(e, 6, !1, e, null, !1, !1);
});
["rowSpan", "start"].forEach(function(e) {
  Me[e] = new Re(e, 5, !1, e.toLowerCase(), null, !1, !1);
});
var Ou = /[\-:]([a-z])/g;
function Fu(e) {
  return e[1].toUpperCase();
}
"accent-height alignment-baseline arabic-form baseline-shift cap-height clip-path clip-rule color-interpolation color-interpolation-filters color-profile color-rendering dominant-baseline enable-background fill-opacity fill-rule flood-color flood-opacity font-family font-size font-size-adjust font-stretch font-style font-variant font-weight glyph-name glyph-orientation-horizontal glyph-orientation-vertical horiz-adv-x horiz-origin-x image-rendering letter-spacing lighting-color marker-end marker-mid marker-start overline-position overline-thickness paint-order panose-1 pointer-events rendering-intent shape-rendering stop-color stop-opacity strikethrough-position strikethrough-thickness stroke-dasharray stroke-dashoffset stroke-linecap stroke-linejoin stroke-miterlimit stroke-opacity stroke-width text-anchor text-decoration text-rendering underline-position underline-thickness unicode-bidi unicode-range units-per-em v-alphabetic v-hanging v-ideographic v-mathematical vector-effect vert-adv-y vert-origin-x vert-origin-y word-spacing writing-mode xmlns:xlink x-height".split(" ").forEach(function(e) {
  var t = e.replace(
    Ou,
    Fu
  );
  Me[t] = new Re(t, 1, !1, e, null, !1, !1);
});
"xlink:actuate xlink:arcrole xlink:role xlink:show xlink:title xlink:type".split(" ").forEach(function(e) {
  var t = e.replace(Ou, Fu);
  Me[t] = new Re(t, 1, !1, e, "http://www.w3.org/1999/xlink", !1, !1);
});
["xml:base", "xml:lang", "xml:space"].forEach(function(e) {
  var t = e.replace(Ou, Fu);
  Me[t] = new Re(t, 1, !1, e, "http://www.w3.org/XML/1998/namespace", !1, !1);
});
["tabIndex", "crossOrigin"].forEach(function(e) {
  Me[e] = new Re(e, 1, !1, e.toLowerCase(), null, !1, !1);
});
Me.xlinkHref = new Re("xlinkHref", 1, !1, "xlink:href", "http://www.w3.org/1999/xlink", !0, !1);
["src", "href", "action", "formAction"].forEach(function(e) {
  Me[e] = new Re(e, 1, !1, e.toLowerCase(), null, !0, !0);
});
function Hu(e, t, n, r) {
  var o = Me.hasOwnProperty(t) ? Me[t] : null;
  (o !== null ? o.type !== 0 : r || !(2 < t.length) || t[0] !== "o" && t[0] !== "O" || t[1] !== "n" && t[1] !== "N") && (Q0(t, n, o, r) && (n = null), r || o === null ? Y0(t) && (n === null ? e.removeAttribute(t) : e.setAttribute(t, "" + n)) : o.mustUseProperty ? e[o.propertyName] = n === null ? o.type === 3 ? !1 : "" : n : (t = o.attributeName, r = o.attributeNamespace, n === null ? e.removeAttribute(t) : (o = o.type, n = o === 3 || o === 4 && n === !0 ? "" : "" + n, r ? e.setAttributeNS(r, t, n) : e.setAttribute(t, n))));
}
var jt = U0.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED, Uo = Symbol.for("react.element"), jn = Symbol.for("react.portal"), On = Symbol.for("react.fragment"), Vu = Symbol.for("react.strict_mode"), Ml = Symbol.for("react.profiler"), zd = Symbol.for("react.provider"), Id = Symbol.for("react.context"), Bu = Symbol.for("react.forward_ref"), Pl = Symbol.for("react.suspense"), zl = Symbol.for("react.suspense_list"), Uu = Symbol.for("react.memo"), Ut = Symbol.for("react.lazy"), Td = Symbol.for("react.offscreen"), ic = Symbol.iterator;
function Pr(e) {
  return e === null || typeof e != "object" ? null : (e = ic && e[ic] || e["@@iterator"], typeof e == "function" ? e : null);
}
var me = Object.assign, Ys;
function Or(e) {
  if (Ys === void 0) try {
    throw Error();
  } catch (n) {
    var t = n.stack.trim().match(/\n( *(at )?)/);
    Ys = t && t[1] || "";
  }
  return `
` + Ys + e;
}
var Xs = !1;
function Qs(e, t) {
  if (!e || Xs) return "";
  Xs = !0;
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
      } catch (a) {
        var r = a;
      }
      Reflect.construct(e, [], t);
    } else {
      try {
        t.call();
      } catch (a) {
        r = a;
      }
      e.call(t.prototype);
    }
    else {
      try {
        throw Error();
      } catch (a) {
        r = a;
      }
      e();
    }
  } catch (a) {
    if (a && r && typeof a.stack == "string") {
      for (var o = a.stack.split(`
`), i = r.stack.split(`
`), s = o.length - 1, l = i.length - 1; 1 <= s && 0 <= l && o[s] !== i[l]; ) l--;
      for (; 1 <= s && 0 <= l; s--, l--) if (o[s] !== i[l]) {
        if (s !== 1 || l !== 1)
          do
            if (s--, l--, 0 > l || o[s] !== i[l]) {
              var u = `
` + o[s].replace(" at new ", " at ");
              return e.displayName && u.includes("<anonymous>") && (u = u.replace("<anonymous>", e.displayName)), u;
            }
          while (1 <= s && 0 <= l);
        break;
      }
    }
  } finally {
    Xs = !1, Error.prepareStackTrace = n;
  }
  return (e = e ? e.displayName || e.name : "") ? Or(e) : "";
}
function b0(e) {
  switch (e.tag) {
    case 5:
      return Or(e.type);
    case 16:
      return Or("Lazy");
    case 13:
      return Or("Suspense");
    case 19:
      return Or("SuspenseList");
    case 0:
    case 2:
    case 15:
      return e = Qs(e.type, !1), e;
    case 11:
      return e = Qs(e.type.render, !1), e;
    case 1:
      return e = Qs(e.type, !0), e;
    default:
      return "";
  }
}
function Il(e) {
  if (e == null) return null;
  if (typeof e == "function") return e.displayName || e.name || null;
  if (typeof e == "string") return e;
  switch (e) {
    case On:
      return "Fragment";
    case jn:
      return "Portal";
    case Ml:
      return "Profiler";
    case Vu:
      return "StrictMode";
    case Pl:
      return "Suspense";
    case zl:
      return "SuspenseList";
  }
  if (typeof e == "object") switch (e.$$typeof) {
    case Id:
      return (e.displayName || "Context") + ".Consumer";
    case zd:
      return (e._context.displayName || "Context") + ".Provider";
    case Bu:
      var t = e.render;
      return e = e.displayName, e || (e = t.displayName || t.name || "", e = e !== "" ? "ForwardRef(" + e + ")" : "ForwardRef"), e;
    case Uu:
      return t = e.displayName || null, t !== null ? t : Il(e.type) || "Memo";
    case Ut:
      t = e._payload, e = e._init;
      try {
        return Il(e(t));
      } catch {
      }
  }
  return null;
}
function K0(e) {
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
      return Il(t);
    case 8:
      return t === Vu ? "StrictMode" : "Mode";
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
function sn(e) {
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
function Ld(e) {
  var t = e.type;
  return (e = e.nodeName) && e.toLowerCase() === "input" && (t === "checkbox" || t === "radio");
}
function G0(e) {
  var t = Ld(e) ? "checked" : "value", n = Object.getOwnPropertyDescriptor(e.constructor.prototype, t), r = "" + e[t];
  if (!e.hasOwnProperty(t) && typeof n < "u" && typeof n.get == "function" && typeof n.set == "function") {
    var o = n.get, i = n.set;
    return Object.defineProperty(e, t, { configurable: !0, get: function() {
      return o.call(this);
    }, set: function(s) {
      r = "" + s, i.call(this, s);
    } }), Object.defineProperty(e, t, { enumerable: n.enumerable }), { getValue: function() {
      return r;
    }, setValue: function(s) {
      r = "" + s;
    }, stopTracking: function() {
      e._valueTracker = null, delete e[t];
    } };
  }
}
function Wo(e) {
  e._valueTracker || (e._valueTracker = G0(e));
}
function Ad(e) {
  if (!e) return !1;
  var t = e._valueTracker;
  if (!t) return !0;
  var n = t.getValue(), r = "";
  return e && (r = Ld(e) ? e.checked ? "true" : "false" : e.value), e = r, e !== n ? (t.setValue(e), !0) : !1;
}
function Ti(e) {
  if (e = e || (typeof document < "u" ? document : void 0), typeof e > "u") return null;
  try {
    return e.activeElement || e.body;
  } catch {
    return e.body;
  }
}
function Tl(e, t) {
  var n = t.checked;
  return me({}, t, { defaultChecked: void 0, defaultValue: void 0, value: void 0, checked: n ?? e._wrapperState.initialChecked });
}
function sc(e, t) {
  var n = t.defaultValue == null ? "" : t.defaultValue, r = t.checked != null ? t.checked : t.defaultChecked;
  n = sn(t.value != null ? t.value : n), e._wrapperState = { initialChecked: r, initialValue: n, controlled: t.type === "checkbox" || t.type === "radio" ? t.checked != null : t.value != null };
}
function $d(e, t) {
  t = t.checked, t != null && Hu(e, "checked", t, !1);
}
function Ll(e, t) {
  $d(e, t);
  var n = sn(t.value), r = t.type;
  if (n != null) r === "number" ? (n === 0 && e.value === "" || e.value != n) && (e.value = "" + n) : e.value !== "" + n && (e.value = "" + n);
  else if (r === "submit" || r === "reset") {
    e.removeAttribute("value");
    return;
  }
  t.hasOwnProperty("value") ? Al(e, t.type, n) : t.hasOwnProperty("defaultValue") && Al(e, t.type, sn(t.defaultValue)), t.checked == null && t.defaultChecked != null && (e.defaultChecked = !!t.defaultChecked);
}
function lc(e, t, n) {
  if (t.hasOwnProperty("value") || t.hasOwnProperty("defaultValue")) {
    var r = t.type;
    if (!(r !== "submit" && r !== "reset" || t.value !== void 0 && t.value !== null)) return;
    t = "" + e._wrapperState.initialValue, n || t === e.value || (e.value = t), e.defaultValue = t;
  }
  n = e.name, n !== "" && (e.name = ""), e.defaultChecked = !!e._wrapperState.initialChecked, n !== "" && (e.name = n);
}
function Al(e, t, n) {
  (t !== "number" || Ti(e.ownerDocument) !== e) && (n == null ? e.defaultValue = "" + e._wrapperState.initialValue : e.defaultValue !== "" + n && (e.defaultValue = "" + n));
}
var Fr = Array.isArray;
function Zn(e, t, n, r) {
  if (e = e.options, t) {
    t = {};
    for (var o = 0; o < n.length; o++) t["$" + n[o]] = !0;
    for (n = 0; n < e.length; n++) o = t.hasOwnProperty("$" + e[n].value), e[n].selected !== o && (e[n].selected = o), o && r && (e[n].defaultSelected = !0);
  } else {
    for (n = "" + sn(n), t = null, o = 0; o < e.length; o++) {
      if (e[o].value === n) {
        e[o].selected = !0, r && (e[o].defaultSelected = !0);
        return;
      }
      t !== null || e[o].disabled || (t = e[o]);
    }
    t !== null && (t.selected = !0);
  }
}
function $l(e, t) {
  if (t.dangerouslySetInnerHTML != null) throw Error(V(91));
  return me({}, t, { value: void 0, defaultValue: void 0, children: "" + e._wrapperState.initialValue });
}
function uc(e, t) {
  var n = t.value;
  if (n == null) {
    if (n = t.children, t = t.defaultValue, n != null) {
      if (t != null) throw Error(V(92));
      if (Fr(n)) {
        if (1 < n.length) throw Error(V(93));
        n = n[0];
      }
      t = n;
    }
    t == null && (t = ""), n = t;
  }
  e._wrapperState = { initialValue: sn(n) };
}
function Dd(e, t) {
  var n = sn(t.value), r = sn(t.defaultValue);
  n != null && (n = "" + n, n !== e.value && (e.value = n), t.defaultValue == null && e.defaultValue !== n && (e.defaultValue = n)), r != null && (e.defaultValue = "" + r);
}
function ac(e) {
  var t = e.textContent;
  t === e._wrapperState.initialValue && t !== "" && t !== null && (e.value = t);
}
function Rd(e) {
  switch (e) {
    case "svg":
      return "http://www.w3.org/2000/svg";
    case "math":
      return "http://www.w3.org/1998/Math/MathML";
    default:
      return "http://www.w3.org/1999/xhtml";
  }
}
function Dl(e, t) {
  return e == null || e === "http://www.w3.org/1999/xhtml" ? Rd(t) : e === "http://www.w3.org/2000/svg" && t === "foreignObject" ? "http://www.w3.org/1999/xhtml" : e;
}
var Yo, jd = function(e) {
  return typeof MSApp < "u" && MSApp.execUnsafeLocalFunction ? function(t, n, r, o) {
    MSApp.execUnsafeLocalFunction(function() {
      return e(t, n, r, o);
    });
  } : e;
}(function(e, t) {
  if (e.namespaceURI !== "http://www.w3.org/2000/svg" || "innerHTML" in e) e.innerHTML = t;
  else {
    for (Yo = Yo || document.createElement("div"), Yo.innerHTML = "<svg>" + t.valueOf().toString() + "</svg>", t = Yo.firstChild; e.firstChild; ) e.removeChild(e.firstChild);
    for (; t.firstChild; ) e.appendChild(t.firstChild);
  }
});
function no(e, t) {
  if (t) {
    var n = e.firstChild;
    if (n && n === e.lastChild && n.nodeType === 3) {
      n.nodeValue = t;
      return;
    }
  }
  e.textContent = t;
}
var Wr = {
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
}, Z0 = ["Webkit", "ms", "Moz", "O"];
Object.keys(Wr).forEach(function(e) {
  Z0.forEach(function(t) {
    t = t + e.charAt(0).toUpperCase() + e.substring(1), Wr[t] = Wr[e];
  });
});
function Od(e, t, n) {
  return t == null || typeof t == "boolean" || t === "" ? "" : n || typeof t != "number" || t === 0 || Wr.hasOwnProperty(e) && Wr[e] ? ("" + t).trim() : t + "px";
}
function Fd(e, t) {
  e = e.style;
  for (var n in t) if (t.hasOwnProperty(n)) {
    var r = n.indexOf("--") === 0, o = Od(n, t[n], r);
    n === "float" && (n = "cssFloat"), r ? e.setProperty(n, o) : e[n] = o;
  }
}
var q0 = me({ menuitem: !0 }, { area: !0, base: !0, br: !0, col: !0, embed: !0, hr: !0, img: !0, input: !0, keygen: !0, link: !0, meta: !0, param: !0, source: !0, track: !0, wbr: !0 });
function Rl(e, t) {
  if (t) {
    if (q0[e] && (t.children != null || t.dangerouslySetInnerHTML != null)) throw Error(V(137, e));
    if (t.dangerouslySetInnerHTML != null) {
      if (t.children != null) throw Error(V(60));
      if (typeof t.dangerouslySetInnerHTML != "object" || !("__html" in t.dangerouslySetInnerHTML)) throw Error(V(61));
    }
    if (t.style != null && typeof t.style != "object") throw Error(V(62));
  }
}
function jl(e, t) {
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
var Ol = null;
function Wu(e) {
  return e = e.target || e.srcElement || window, e.correspondingUseElement && (e = e.correspondingUseElement), e.nodeType === 3 ? e.parentNode : e;
}
var Fl = null, qn = null, Jn = null;
function cc(e) {
  if (e = Lo(e)) {
    if (typeof Fl != "function") throw Error(V(280));
    var t = e.stateNode;
    t && (t = ms(t), Fl(e.stateNode, e.type, t));
  }
}
function Hd(e) {
  qn ? Jn ? Jn.push(e) : Jn = [e] : qn = e;
}
function Vd() {
  if (qn) {
    var e = qn, t = Jn;
    if (Jn = qn = null, cc(e), t) for (e = 0; e < t.length; e++) cc(t[e]);
  }
}
function Bd(e, t) {
  return e(t);
}
function Ud() {
}
var bs = !1;
function Wd(e, t, n) {
  if (bs) return e(t, n);
  bs = !0;
  try {
    return Bd(e, t, n);
  } finally {
    bs = !1, (qn !== null || Jn !== null) && (Ud(), Vd());
  }
}
function ro(e, t) {
  var n = e.stateNode;
  if (n === null) return null;
  var r = ms(n);
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
  if (n && typeof n != "function") throw Error(V(231, t, typeof n));
  return n;
}
var Hl = !1;
if (Lt) try {
  var zr = {};
  Object.defineProperty(zr, "passive", { get: function() {
    Hl = !0;
  } }), window.addEventListener("test", zr, zr), window.removeEventListener("test", zr, zr);
} catch {
  Hl = !1;
}
function J0(e, t, n, r, o, i, s, l, u) {
  var a = Array.prototype.slice.call(arguments, 3);
  try {
    t.apply(n, a);
  } catch (d) {
    this.onError(d);
  }
}
var Yr = !1, Li = null, Ai = !1, Vl = null, ey = { onError: function(e) {
  Yr = !0, Li = e;
} };
function ty(e, t, n, r, o, i, s, l, u) {
  Yr = !1, Li = null, J0.apply(ey, arguments);
}
function ny(e, t, n, r, o, i, s, l, u) {
  if (ty.apply(this, arguments), Yr) {
    if (Yr) {
      var a = Li;
      Yr = !1, Li = null;
    } else throw Error(V(198));
    Ai || (Ai = !0, Vl = a);
  }
}
function An(e) {
  var t = e, n = e;
  if (e.alternate) for (; t.return; ) t = t.return;
  else {
    e = t;
    do
      t = e, t.flags & 4098 && (n = t.return), e = t.return;
    while (e);
  }
  return t.tag === 3 ? n : null;
}
function Yd(e) {
  if (e.tag === 13) {
    var t = e.memoizedState;
    if (t === null && (e = e.alternate, e !== null && (t = e.memoizedState)), t !== null) return t.dehydrated;
  }
  return null;
}
function fc(e) {
  if (An(e) !== e) throw Error(V(188));
}
function ry(e) {
  var t = e.alternate;
  if (!t) {
    if (t = An(e), t === null) throw Error(V(188));
    return t !== e ? null : e;
  }
  for (var n = e, r = t; ; ) {
    var o = n.return;
    if (o === null) break;
    var i = o.alternate;
    if (i === null) {
      if (r = o.return, r !== null) {
        n = r;
        continue;
      }
      break;
    }
    if (o.child === i.child) {
      for (i = o.child; i; ) {
        if (i === n) return fc(o), e;
        if (i === r) return fc(o), t;
        i = i.sibling;
      }
      throw Error(V(188));
    }
    if (n.return !== r.return) n = o, r = i;
    else {
      for (var s = !1, l = o.child; l; ) {
        if (l === n) {
          s = !0, n = o, r = i;
          break;
        }
        if (l === r) {
          s = !0, r = o, n = i;
          break;
        }
        l = l.sibling;
      }
      if (!s) {
        for (l = i.child; l; ) {
          if (l === n) {
            s = !0, n = i, r = o;
            break;
          }
          if (l === r) {
            s = !0, r = i, n = o;
            break;
          }
          l = l.sibling;
        }
        if (!s) throw Error(V(189));
      }
    }
    if (n.alternate !== r) throw Error(V(190));
  }
  if (n.tag !== 3) throw Error(V(188));
  return n.stateNode.current === n ? e : t;
}
function Xd(e) {
  return e = ry(e), e !== null ? Qd(e) : null;
}
function Qd(e) {
  if (e.tag === 5 || e.tag === 6) return e;
  for (e = e.child; e !== null; ) {
    var t = Qd(e);
    if (t !== null) return t;
    e = e.sibling;
  }
  return null;
}
var bd = Qe.unstable_scheduleCallback, dc = Qe.unstable_cancelCallback, oy = Qe.unstable_shouldYield, iy = Qe.unstable_requestPaint, ve = Qe.unstable_now, sy = Qe.unstable_getCurrentPriorityLevel, Yu = Qe.unstable_ImmediatePriority, Kd = Qe.unstable_UserBlockingPriority, $i = Qe.unstable_NormalPriority, ly = Qe.unstable_LowPriority, Gd = Qe.unstable_IdlePriority, ds = null, wt = null;
function uy(e) {
  if (wt && typeof wt.onCommitFiberRoot == "function") try {
    wt.onCommitFiberRoot(ds, e, void 0, (e.current.flags & 128) === 128);
  } catch {
  }
}
var ft = Math.clz32 ? Math.clz32 : fy, ay = Math.log, cy = Math.LN2;
function fy(e) {
  return e >>>= 0, e === 0 ? 32 : 31 - (ay(e) / cy | 0) | 0;
}
var Xo = 64, Qo = 4194304;
function Hr(e) {
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
function Di(e, t) {
  var n = e.pendingLanes;
  if (n === 0) return 0;
  var r = 0, o = e.suspendedLanes, i = e.pingedLanes, s = n & 268435455;
  if (s !== 0) {
    var l = s & ~o;
    l !== 0 ? r = Hr(l) : (i &= s, i !== 0 && (r = Hr(i)));
  } else s = n & ~o, s !== 0 ? r = Hr(s) : i !== 0 && (r = Hr(i));
  if (r === 0) return 0;
  if (t !== 0 && t !== r && !(t & o) && (o = r & -r, i = t & -t, o >= i || o === 16 && (i & 4194240) !== 0)) return t;
  if (r & 4 && (r |= n & 16), t = e.entangledLanes, t !== 0) for (e = e.entanglements, t &= r; 0 < t; ) n = 31 - ft(t), o = 1 << n, r |= e[n], t &= ~o;
  return r;
}
function dy(e, t) {
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
function hy(e, t) {
  for (var n = e.suspendedLanes, r = e.pingedLanes, o = e.expirationTimes, i = e.pendingLanes; 0 < i; ) {
    var s = 31 - ft(i), l = 1 << s, u = o[s];
    u === -1 ? (!(l & n) || l & r) && (o[s] = dy(l, t)) : u <= t && (e.expiredLanes |= l), i &= ~l;
  }
}
function Bl(e) {
  return e = e.pendingLanes & -1073741825, e !== 0 ? e : e & 1073741824 ? 1073741824 : 0;
}
function Zd() {
  var e = Xo;
  return Xo <<= 1, !(Xo & 4194240) && (Xo = 64), e;
}
function Ks(e) {
  for (var t = [], n = 0; 31 > n; n++) t.push(e);
  return t;
}
function Io(e, t, n) {
  e.pendingLanes |= t, t !== 536870912 && (e.suspendedLanes = 0, e.pingedLanes = 0), e = e.eventTimes, t = 31 - ft(t), e[t] = n;
}
function py(e, t) {
  var n = e.pendingLanes & ~t;
  e.pendingLanes = t, e.suspendedLanes = 0, e.pingedLanes = 0, e.expiredLanes &= t, e.mutableReadLanes &= t, e.entangledLanes &= t, t = e.entanglements;
  var r = e.eventTimes;
  for (e = e.expirationTimes; 0 < n; ) {
    var o = 31 - ft(n), i = 1 << o;
    t[o] = 0, r[o] = -1, e[o] = -1, n &= ~i;
  }
}
function Xu(e, t) {
  var n = e.entangledLanes |= t;
  for (e = e.entanglements; n; ) {
    var r = 31 - ft(n), o = 1 << r;
    o & t | e[r] & t && (e[r] |= t), n &= ~o;
  }
}
var se = 0;
function qd(e) {
  return e &= -e, 1 < e ? 4 < e ? e & 268435455 ? 16 : 536870912 : 4 : 1;
}
var Jd, Qu, eh, th, nh, Ul = !1, bo = [], Gt = null, Zt = null, qt = null, oo = /* @__PURE__ */ new Map(), io = /* @__PURE__ */ new Map(), Yt = [], gy = "mousedown mouseup touchcancel touchend touchstart auxclick dblclick pointercancel pointerdown pointerup dragend dragstart drop compositionend compositionstart keydown keypress keyup input textInput copy cut paste click change contextmenu reset submit".split(" ");
function hc(e, t) {
  switch (e) {
    case "focusin":
    case "focusout":
      Gt = null;
      break;
    case "dragenter":
    case "dragleave":
      Zt = null;
      break;
    case "mouseover":
    case "mouseout":
      qt = null;
      break;
    case "pointerover":
    case "pointerout":
      oo.delete(t.pointerId);
      break;
    case "gotpointercapture":
    case "lostpointercapture":
      io.delete(t.pointerId);
  }
}
function Ir(e, t, n, r, o, i) {
  return e === null || e.nativeEvent !== i ? (e = { blockedOn: t, domEventName: n, eventSystemFlags: r, nativeEvent: i, targetContainers: [o] }, t !== null && (t = Lo(t), t !== null && Qu(t)), e) : (e.eventSystemFlags |= r, t = e.targetContainers, o !== null && t.indexOf(o) === -1 && t.push(o), e);
}
function my(e, t, n, r, o) {
  switch (t) {
    case "focusin":
      return Gt = Ir(Gt, e, t, n, r, o), !0;
    case "dragenter":
      return Zt = Ir(Zt, e, t, n, r, o), !0;
    case "mouseover":
      return qt = Ir(qt, e, t, n, r, o), !0;
    case "pointerover":
      var i = o.pointerId;
      return oo.set(i, Ir(oo.get(i) || null, e, t, n, r, o)), !0;
    case "gotpointercapture":
      return i = o.pointerId, io.set(i, Ir(io.get(i) || null, e, t, n, r, o)), !0;
  }
  return !1;
}
function rh(e) {
  var t = gn(e.target);
  if (t !== null) {
    var n = An(t);
    if (n !== null) {
      if (t = n.tag, t === 13) {
        if (t = Yd(n), t !== null) {
          e.blockedOn = t, nh(e.priority, function() {
            eh(n);
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
function mi(e) {
  if (e.blockedOn !== null) return !1;
  for (var t = e.targetContainers; 0 < t.length; ) {
    var n = Wl(e.domEventName, e.eventSystemFlags, t[0], e.nativeEvent);
    if (n === null) {
      n = e.nativeEvent;
      var r = new n.constructor(n.type, n);
      Ol = r, n.target.dispatchEvent(r), Ol = null;
    } else return t = Lo(n), t !== null && Qu(t), e.blockedOn = n, !1;
    t.shift();
  }
  return !0;
}
function pc(e, t, n) {
  mi(e) && n.delete(t);
}
function yy() {
  Ul = !1, Gt !== null && mi(Gt) && (Gt = null), Zt !== null && mi(Zt) && (Zt = null), qt !== null && mi(qt) && (qt = null), oo.forEach(pc), io.forEach(pc);
}
function Tr(e, t) {
  e.blockedOn === t && (e.blockedOn = null, Ul || (Ul = !0, Qe.unstable_scheduleCallback(Qe.unstable_NormalPriority, yy)));
}
function so(e) {
  function t(o) {
    return Tr(o, e);
  }
  if (0 < bo.length) {
    Tr(bo[0], e);
    for (var n = 1; n < bo.length; n++) {
      var r = bo[n];
      r.blockedOn === e && (r.blockedOn = null);
    }
  }
  for (Gt !== null && Tr(Gt, e), Zt !== null && Tr(Zt, e), qt !== null && Tr(qt, e), oo.forEach(t), io.forEach(t), n = 0; n < Yt.length; n++) r = Yt[n], r.blockedOn === e && (r.blockedOn = null);
  for (; 0 < Yt.length && (n = Yt[0], n.blockedOn === null); ) rh(n), n.blockedOn === null && Yt.shift();
}
var er = jt.ReactCurrentBatchConfig, Ri = !0;
function vy(e, t, n, r) {
  var o = se, i = er.transition;
  er.transition = null;
  try {
    se = 1, bu(e, t, n, r);
  } finally {
    se = o, er.transition = i;
  }
}
function wy(e, t, n, r) {
  var o = se, i = er.transition;
  er.transition = null;
  try {
    se = 4, bu(e, t, n, r);
  } finally {
    se = o, er.transition = i;
  }
}
function bu(e, t, n, r) {
  if (Ri) {
    var o = Wl(e, t, n, r);
    if (o === null) il(e, t, r, ji, n), hc(e, r);
    else if (my(o, e, t, n, r)) r.stopPropagation();
    else if (hc(e, r), t & 4 && -1 < gy.indexOf(e)) {
      for (; o !== null; ) {
        var i = Lo(o);
        if (i !== null && Jd(i), i = Wl(e, t, n, r), i === null && il(e, t, r, ji, n), i === o) break;
        o = i;
      }
      o !== null && r.stopPropagation();
    } else il(e, t, r, null, n);
  }
}
var ji = null;
function Wl(e, t, n, r) {
  if (ji = null, e = Wu(r), e = gn(e), e !== null) if (t = An(e), t === null) e = null;
  else if (n = t.tag, n === 13) {
    if (e = Yd(t), e !== null) return e;
    e = null;
  } else if (n === 3) {
    if (t.stateNode.current.memoizedState.isDehydrated) return t.tag === 3 ? t.stateNode.containerInfo : null;
    e = null;
  } else t !== e && (e = null);
  return ji = e, null;
}
function oh(e) {
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
      switch (sy()) {
        case Yu:
          return 1;
        case Kd:
          return 4;
        case $i:
        case ly:
          return 16;
        case Gd:
          return 536870912;
        default:
          return 16;
      }
    default:
      return 16;
  }
}
var bt = null, Ku = null, yi = null;
function ih() {
  if (yi) return yi;
  var e, t = Ku, n = t.length, r, o = "value" in bt ? bt.value : bt.textContent, i = o.length;
  for (e = 0; e < n && t[e] === o[e]; e++) ;
  var s = n - e;
  for (r = 1; r <= s && t[n - r] === o[i - r]; r++) ;
  return yi = o.slice(e, 1 < r ? 1 - r : void 0);
}
function vi(e) {
  var t = e.keyCode;
  return "charCode" in e ? (e = e.charCode, e === 0 && t === 13 && (e = 13)) : e = t, e === 10 && (e = 13), 32 <= e || e === 13 ? e : 0;
}
function Ko() {
  return !0;
}
function gc() {
  return !1;
}
function Ge(e) {
  function t(n, r, o, i, s) {
    this._reactName = n, this._targetInst = o, this.type = r, this.nativeEvent = i, this.target = s, this.currentTarget = null;
    for (var l in e) e.hasOwnProperty(l) && (n = e[l], this[l] = n ? n(i) : i[l]);
    return this.isDefaultPrevented = (i.defaultPrevented != null ? i.defaultPrevented : i.returnValue === !1) ? Ko : gc, this.isPropagationStopped = gc, this;
  }
  return me(t.prototype, { preventDefault: function() {
    this.defaultPrevented = !0;
    var n = this.nativeEvent;
    n && (n.preventDefault ? n.preventDefault() : typeof n.returnValue != "unknown" && (n.returnValue = !1), this.isDefaultPrevented = Ko);
  }, stopPropagation: function() {
    var n = this.nativeEvent;
    n && (n.stopPropagation ? n.stopPropagation() : typeof n.cancelBubble != "unknown" && (n.cancelBubble = !0), this.isPropagationStopped = Ko);
  }, persist: function() {
  }, isPersistent: Ko }), t;
}
var _r = { eventPhase: 0, bubbles: 0, cancelable: 0, timeStamp: function(e) {
  return e.timeStamp || Date.now();
}, defaultPrevented: 0, isTrusted: 0 }, Gu = Ge(_r), To = me({}, _r, { view: 0, detail: 0 }), xy = Ge(To), Gs, Zs, Lr, hs = me({}, To, { screenX: 0, screenY: 0, clientX: 0, clientY: 0, pageX: 0, pageY: 0, ctrlKey: 0, shiftKey: 0, altKey: 0, metaKey: 0, getModifierState: Zu, button: 0, buttons: 0, relatedTarget: function(e) {
  return e.relatedTarget === void 0 ? e.fromElement === e.srcElement ? e.toElement : e.fromElement : e.relatedTarget;
}, movementX: function(e) {
  return "movementX" in e ? e.movementX : (e !== Lr && (Lr && e.type === "mousemove" ? (Gs = e.screenX - Lr.screenX, Zs = e.screenY - Lr.screenY) : Zs = Gs = 0, Lr = e), Gs);
}, movementY: function(e) {
  return "movementY" in e ? e.movementY : Zs;
} }), mc = Ge(hs), Sy = me({}, hs, { dataTransfer: 0 }), _y = Ge(Sy), Ey = me({}, To, { relatedTarget: 0 }), qs = Ge(Ey), ky = me({}, _r, { animationName: 0, elapsedTime: 0, pseudoElement: 0 }), Ny = Ge(ky), Cy = me({}, _r, { clipboardData: function(e) {
  return "clipboardData" in e ? e.clipboardData : window.clipboardData;
} }), My = Ge(Cy), Py = me({}, _r, { data: 0 }), yc = Ge(Py), zy = {
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
}, Iy = {
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
}, Ty = { Alt: "altKey", Control: "ctrlKey", Meta: "metaKey", Shift: "shiftKey" };
function Ly(e) {
  var t = this.nativeEvent;
  return t.getModifierState ? t.getModifierState(e) : (e = Ty[e]) ? !!t[e] : !1;
}
function Zu() {
  return Ly;
}
var Ay = me({}, To, { key: function(e) {
  if (e.key) {
    var t = zy[e.key] || e.key;
    if (t !== "Unidentified") return t;
  }
  return e.type === "keypress" ? (e = vi(e), e === 13 ? "Enter" : String.fromCharCode(e)) : e.type === "keydown" || e.type === "keyup" ? Iy[e.keyCode] || "Unidentified" : "";
}, code: 0, location: 0, ctrlKey: 0, shiftKey: 0, altKey: 0, metaKey: 0, repeat: 0, locale: 0, getModifierState: Zu, charCode: function(e) {
  return e.type === "keypress" ? vi(e) : 0;
}, keyCode: function(e) {
  return e.type === "keydown" || e.type === "keyup" ? e.keyCode : 0;
}, which: function(e) {
  return e.type === "keypress" ? vi(e) : e.type === "keydown" || e.type === "keyup" ? e.keyCode : 0;
} }), $y = Ge(Ay), Dy = me({}, hs, { pointerId: 0, width: 0, height: 0, pressure: 0, tangentialPressure: 0, tiltX: 0, tiltY: 0, twist: 0, pointerType: 0, isPrimary: 0 }), vc = Ge(Dy), Ry = me({}, To, { touches: 0, targetTouches: 0, changedTouches: 0, altKey: 0, metaKey: 0, ctrlKey: 0, shiftKey: 0, getModifierState: Zu }), jy = Ge(Ry), Oy = me({}, _r, { propertyName: 0, elapsedTime: 0, pseudoElement: 0 }), Fy = Ge(Oy), Hy = me({}, hs, {
  deltaX: function(e) {
    return "deltaX" in e ? e.deltaX : "wheelDeltaX" in e ? -e.wheelDeltaX : 0;
  },
  deltaY: function(e) {
    return "deltaY" in e ? e.deltaY : "wheelDeltaY" in e ? -e.wheelDeltaY : "wheelDelta" in e ? -e.wheelDelta : 0;
  },
  deltaZ: 0,
  deltaMode: 0
}), Vy = Ge(Hy), By = [9, 13, 27, 32], qu = Lt && "CompositionEvent" in window, Xr = null;
Lt && "documentMode" in document && (Xr = document.documentMode);
var Uy = Lt && "TextEvent" in window && !Xr, sh = Lt && (!qu || Xr && 8 < Xr && 11 >= Xr), wc = " ", xc = !1;
function lh(e, t) {
  switch (e) {
    case "keyup":
      return By.indexOf(t.keyCode) !== -1;
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
function uh(e) {
  return e = e.detail, typeof e == "object" && "data" in e ? e.data : null;
}
var Fn = !1;
function Wy(e, t) {
  switch (e) {
    case "compositionend":
      return uh(t);
    case "keypress":
      return t.which !== 32 ? null : (xc = !0, wc);
    case "textInput":
      return e = t.data, e === wc && xc ? null : e;
    default:
      return null;
  }
}
function Yy(e, t) {
  if (Fn) return e === "compositionend" || !qu && lh(e, t) ? (e = ih(), yi = Ku = bt = null, Fn = !1, e) : null;
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
      return sh && t.locale !== "ko" ? null : t.data;
    default:
      return null;
  }
}
var Xy = { color: !0, date: !0, datetime: !0, "datetime-local": !0, email: !0, month: !0, number: !0, password: !0, range: !0, search: !0, tel: !0, text: !0, time: !0, url: !0, week: !0 };
function Sc(e) {
  var t = e && e.nodeName && e.nodeName.toLowerCase();
  return t === "input" ? !!Xy[e.type] : t === "textarea";
}
function ah(e, t, n, r) {
  Hd(r), t = Oi(t, "onChange"), 0 < t.length && (n = new Gu("onChange", "change", null, n, r), e.push({ event: n, listeners: t }));
}
var Qr = null, lo = null;
function Qy(e) {
  xh(e, 0);
}
function ps(e) {
  var t = Bn(e);
  if (Ad(t)) return e;
}
function by(e, t) {
  if (e === "change") return t;
}
var ch = !1;
if (Lt) {
  var Js;
  if (Lt) {
    var el = "oninput" in document;
    if (!el) {
      var _c = document.createElement("div");
      _c.setAttribute("oninput", "return;"), el = typeof _c.oninput == "function";
    }
    Js = el;
  } else Js = !1;
  ch = Js && (!document.documentMode || 9 < document.documentMode);
}
function Ec() {
  Qr && (Qr.detachEvent("onpropertychange", fh), lo = Qr = null);
}
function fh(e) {
  if (e.propertyName === "value" && ps(lo)) {
    var t = [];
    ah(t, lo, e, Wu(e)), Wd(Qy, t);
  }
}
function Ky(e, t, n) {
  e === "focusin" ? (Ec(), Qr = t, lo = n, Qr.attachEvent("onpropertychange", fh)) : e === "focusout" && Ec();
}
function Gy(e) {
  if (e === "selectionchange" || e === "keyup" || e === "keydown") return ps(lo);
}
function Zy(e, t) {
  if (e === "click") return ps(t);
}
function qy(e, t) {
  if (e === "input" || e === "change") return ps(t);
}
function Jy(e, t) {
  return e === t && (e !== 0 || 1 / e === 1 / t) || e !== e && t !== t;
}
var ht = typeof Object.is == "function" ? Object.is : Jy;
function uo(e, t) {
  if (ht(e, t)) return !0;
  if (typeof e != "object" || e === null || typeof t != "object" || t === null) return !1;
  var n = Object.keys(e), r = Object.keys(t);
  if (n.length !== r.length) return !1;
  for (r = 0; r < n.length; r++) {
    var o = n[r];
    if (!Cl.call(t, o) || !ht(e[o], t[o])) return !1;
  }
  return !0;
}
function kc(e) {
  for (; e && e.firstChild; ) e = e.firstChild;
  return e;
}
function Nc(e, t) {
  var n = kc(e);
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
    n = kc(n);
  }
}
function dh(e, t) {
  return e && t ? e === t ? !0 : e && e.nodeType === 3 ? !1 : t && t.nodeType === 3 ? dh(e, t.parentNode) : "contains" in e ? e.contains(t) : e.compareDocumentPosition ? !!(e.compareDocumentPosition(t) & 16) : !1 : !1;
}
function hh() {
  for (var e = window, t = Ti(); t instanceof e.HTMLIFrameElement; ) {
    try {
      var n = typeof t.contentWindow.location.href == "string";
    } catch {
      n = !1;
    }
    if (n) e = t.contentWindow;
    else break;
    t = Ti(e.document);
  }
  return t;
}
function Ju(e) {
  var t = e && e.nodeName && e.nodeName.toLowerCase();
  return t && (t === "input" && (e.type === "text" || e.type === "search" || e.type === "tel" || e.type === "url" || e.type === "password") || t === "textarea" || e.contentEditable === "true");
}
function ev(e) {
  var t = hh(), n = e.focusedElem, r = e.selectionRange;
  if (t !== n && n && n.ownerDocument && dh(n.ownerDocument.documentElement, n)) {
    if (r !== null && Ju(n)) {
      if (t = r.start, e = r.end, e === void 0 && (e = t), "selectionStart" in n) n.selectionStart = t, n.selectionEnd = Math.min(e, n.value.length);
      else if (e = (t = n.ownerDocument || document) && t.defaultView || window, e.getSelection) {
        e = e.getSelection();
        var o = n.textContent.length, i = Math.min(r.start, o);
        r = r.end === void 0 ? i : Math.min(r.end, o), !e.extend && i > r && (o = r, r = i, i = o), o = Nc(n, i);
        var s = Nc(
          n,
          r
        );
        o && s && (e.rangeCount !== 1 || e.anchorNode !== o.node || e.anchorOffset !== o.offset || e.focusNode !== s.node || e.focusOffset !== s.offset) && (t = t.createRange(), t.setStart(o.node, o.offset), e.removeAllRanges(), i > r ? (e.addRange(t), e.extend(s.node, s.offset)) : (t.setEnd(s.node, s.offset), e.addRange(t)));
      }
    }
    for (t = [], e = n; e = e.parentNode; ) e.nodeType === 1 && t.push({ element: e, left: e.scrollLeft, top: e.scrollTop });
    for (typeof n.focus == "function" && n.focus(), n = 0; n < t.length; n++) e = t[n], e.element.scrollLeft = e.left, e.element.scrollTop = e.top;
  }
}
var tv = Lt && "documentMode" in document && 11 >= document.documentMode, Hn = null, Yl = null, br = null, Xl = !1;
function Cc(e, t, n) {
  var r = n.window === n ? n.document : n.nodeType === 9 ? n : n.ownerDocument;
  Xl || Hn == null || Hn !== Ti(r) || (r = Hn, "selectionStart" in r && Ju(r) ? r = { start: r.selectionStart, end: r.selectionEnd } : (r = (r.ownerDocument && r.ownerDocument.defaultView || window).getSelection(), r = { anchorNode: r.anchorNode, anchorOffset: r.anchorOffset, focusNode: r.focusNode, focusOffset: r.focusOffset }), br && uo(br, r) || (br = r, r = Oi(Yl, "onSelect"), 0 < r.length && (t = new Gu("onSelect", "select", null, t, n), e.push({ event: t, listeners: r }), t.target = Hn)));
}
function Go(e, t) {
  var n = {};
  return n[e.toLowerCase()] = t.toLowerCase(), n["Webkit" + e] = "webkit" + t, n["Moz" + e] = "moz" + t, n;
}
var Vn = { animationend: Go("Animation", "AnimationEnd"), animationiteration: Go("Animation", "AnimationIteration"), animationstart: Go("Animation", "AnimationStart"), transitionend: Go("Transition", "TransitionEnd") }, tl = {}, ph = {};
Lt && (ph = document.createElement("div").style, "AnimationEvent" in window || (delete Vn.animationend.animation, delete Vn.animationiteration.animation, delete Vn.animationstart.animation), "TransitionEvent" in window || delete Vn.transitionend.transition);
function gs(e) {
  if (tl[e]) return tl[e];
  if (!Vn[e]) return e;
  var t = Vn[e], n;
  for (n in t) if (t.hasOwnProperty(n) && n in ph) return tl[e] = t[n];
  return e;
}
var gh = gs("animationend"), mh = gs("animationiteration"), yh = gs("animationstart"), vh = gs("transitionend"), wh = /* @__PURE__ */ new Map(), Mc = "abort auxClick cancel canPlay canPlayThrough click close contextMenu copy cut drag dragEnd dragEnter dragExit dragLeave dragOver dragStart drop durationChange emptied encrypted ended error gotPointerCapture input invalid keyDown keyPress keyUp load loadedData loadedMetadata loadStart lostPointerCapture mouseDown mouseMove mouseOut mouseOver mouseUp paste pause play playing pointerCancel pointerDown pointerMove pointerOut pointerOver pointerUp progress rateChange reset resize seeked seeking stalled submit suspend timeUpdate touchCancel touchEnd touchStart volumeChange scroll toggle touchMove waiting wheel".split(" ");
function un(e, t) {
  wh.set(e, t), Ln(t, [e]);
}
for (var nl = 0; nl < Mc.length; nl++) {
  var rl = Mc[nl], nv = rl.toLowerCase(), rv = rl[0].toUpperCase() + rl.slice(1);
  un(nv, "on" + rv);
}
un(gh, "onAnimationEnd");
un(mh, "onAnimationIteration");
un(yh, "onAnimationStart");
un("dblclick", "onDoubleClick");
un("focusin", "onFocus");
un("focusout", "onBlur");
un(vh, "onTransitionEnd");
ir("onMouseEnter", ["mouseout", "mouseover"]);
ir("onMouseLeave", ["mouseout", "mouseover"]);
ir("onPointerEnter", ["pointerout", "pointerover"]);
ir("onPointerLeave", ["pointerout", "pointerover"]);
Ln("onChange", "change click focusin focusout input keydown keyup selectionchange".split(" "));
Ln("onSelect", "focusout contextmenu dragend focusin keydown keyup mousedown mouseup selectionchange".split(" "));
Ln("onBeforeInput", ["compositionend", "keypress", "textInput", "paste"]);
Ln("onCompositionEnd", "compositionend focusout keydown keypress keyup mousedown".split(" "));
Ln("onCompositionStart", "compositionstart focusout keydown keypress keyup mousedown".split(" "));
Ln("onCompositionUpdate", "compositionupdate focusout keydown keypress keyup mousedown".split(" "));
var Vr = "abort canplay canplaythrough durationchange emptied encrypted ended error loadeddata loadedmetadata loadstart pause play playing progress ratechange resize seeked seeking stalled suspend timeupdate volumechange waiting".split(" "), ov = new Set("cancel close invalid load scroll toggle".split(" ").concat(Vr));
function Pc(e, t, n) {
  var r = e.type || "unknown-event";
  e.currentTarget = n, ny(r, t, void 0, e), e.currentTarget = null;
}
function xh(e, t) {
  t = (t & 4) !== 0;
  for (var n = 0; n < e.length; n++) {
    var r = e[n], o = r.event;
    r = r.listeners;
    e: {
      var i = void 0;
      if (t) for (var s = r.length - 1; 0 <= s; s--) {
        var l = r[s], u = l.instance, a = l.currentTarget;
        if (l = l.listener, u !== i && o.isPropagationStopped()) break e;
        Pc(o, l, a), i = u;
      }
      else for (s = 0; s < r.length; s++) {
        if (l = r[s], u = l.instance, a = l.currentTarget, l = l.listener, u !== i && o.isPropagationStopped()) break e;
        Pc(o, l, a), i = u;
      }
    }
  }
  if (Ai) throw e = Vl, Ai = !1, Vl = null, e;
}
function ae(e, t) {
  var n = t[Zl];
  n === void 0 && (n = t[Zl] = /* @__PURE__ */ new Set());
  var r = e + "__bubble";
  n.has(r) || (Sh(t, e, 2, !1), n.add(r));
}
function ol(e, t, n) {
  var r = 0;
  t && (r |= 4), Sh(n, e, r, t);
}
var Zo = "_reactListening" + Math.random().toString(36).slice(2);
function ao(e) {
  if (!e[Zo]) {
    e[Zo] = !0, Pd.forEach(function(n) {
      n !== "selectionchange" && (ov.has(n) || ol(n, !1, e), ol(n, !0, e));
    });
    var t = e.nodeType === 9 ? e : e.ownerDocument;
    t === null || t[Zo] || (t[Zo] = !0, ol("selectionchange", !1, t));
  }
}
function Sh(e, t, n, r) {
  switch (oh(t)) {
    case 1:
      var o = vy;
      break;
    case 4:
      o = wy;
      break;
    default:
      o = bu;
  }
  n = o.bind(null, t, n, e), o = void 0, !Hl || t !== "touchstart" && t !== "touchmove" && t !== "wheel" || (o = !0), r ? o !== void 0 ? e.addEventListener(t, n, { capture: !0, passive: o }) : e.addEventListener(t, n, !0) : o !== void 0 ? e.addEventListener(t, n, { passive: o }) : e.addEventListener(t, n, !1);
}
function il(e, t, n, r, o) {
  var i = r;
  if (!(t & 1) && !(t & 2) && r !== null) e: for (; ; ) {
    if (r === null) return;
    var s = r.tag;
    if (s === 3 || s === 4) {
      var l = r.stateNode.containerInfo;
      if (l === o || l.nodeType === 8 && l.parentNode === o) break;
      if (s === 4) for (s = r.return; s !== null; ) {
        var u = s.tag;
        if ((u === 3 || u === 4) && (u = s.stateNode.containerInfo, u === o || u.nodeType === 8 && u.parentNode === o)) return;
        s = s.return;
      }
      for (; l !== null; ) {
        if (s = gn(l), s === null) return;
        if (u = s.tag, u === 5 || u === 6) {
          r = i = s;
          continue e;
        }
        l = l.parentNode;
      }
    }
    r = r.return;
  }
  Wd(function() {
    var a = i, d = Wu(n), c = [];
    e: {
      var f = wh.get(e);
      if (f !== void 0) {
        var m = Gu, y = e;
        switch (e) {
          case "keypress":
            if (vi(n) === 0) break e;
          case "keydown":
          case "keyup":
            m = $y;
            break;
          case "focusin":
            y = "focus", m = qs;
            break;
          case "focusout":
            y = "blur", m = qs;
            break;
          case "beforeblur":
          case "afterblur":
            m = qs;
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
            m = mc;
            break;
          case "drag":
          case "dragend":
          case "dragenter":
          case "dragexit":
          case "dragleave":
          case "dragover":
          case "dragstart":
          case "drop":
            m = _y;
            break;
          case "touchcancel":
          case "touchend":
          case "touchmove":
          case "touchstart":
            m = jy;
            break;
          case gh:
          case mh:
          case yh:
            m = Ny;
            break;
          case vh:
            m = Fy;
            break;
          case "scroll":
            m = xy;
            break;
          case "wheel":
            m = Vy;
            break;
          case "copy":
          case "cut":
          case "paste":
            m = My;
            break;
          case "gotpointercapture":
          case "lostpointercapture":
          case "pointercancel":
          case "pointerdown":
          case "pointermove":
          case "pointerout":
          case "pointerover":
          case "pointerup":
            m = vc;
        }
        var w = (t & 4) !== 0, x = !w && e === "scroll", p = w ? f !== null ? f + "Capture" : null : f;
        w = [];
        for (var g = a, h; g !== null; ) {
          h = g;
          var v = h.stateNode;
          if (h.tag === 5 && v !== null && (h = v, p !== null && (v = ro(g, p), v != null && w.push(co(g, v, h)))), x) break;
          g = g.return;
        }
        0 < w.length && (f = new m(f, y, null, n, d), c.push({ event: f, listeners: w }));
      }
    }
    if (!(t & 7)) {
      e: {
        if (f = e === "mouseover" || e === "pointerover", m = e === "mouseout" || e === "pointerout", f && n !== Ol && (y = n.relatedTarget || n.fromElement) && (gn(y) || y[At])) break e;
        if ((m || f) && (f = d.window === d ? d : (f = d.ownerDocument) ? f.defaultView || f.parentWindow : window, m ? (y = n.relatedTarget || n.toElement, m = a, y = y ? gn(y) : null, y !== null && (x = An(y), y !== x || y.tag !== 5 && y.tag !== 6) && (y = null)) : (m = null, y = a), m !== y)) {
          if (w = mc, v = "onMouseLeave", p = "onMouseEnter", g = "mouse", (e === "pointerout" || e === "pointerover") && (w = vc, v = "onPointerLeave", p = "onPointerEnter", g = "pointer"), x = m == null ? f : Bn(m), h = y == null ? f : Bn(y), f = new w(v, g + "leave", m, n, d), f.target = x, f.relatedTarget = h, v = null, gn(d) === a && (w = new w(p, g + "enter", y, n, d), w.target = h, w.relatedTarget = x, v = w), x = v, m && y) t: {
            for (w = m, p = y, g = 0, h = w; h; h = Dn(h)) g++;
            for (h = 0, v = p; v; v = Dn(v)) h++;
            for (; 0 < g - h; ) w = Dn(w), g--;
            for (; 0 < h - g; ) p = Dn(p), h--;
            for (; g--; ) {
              if (w === p || p !== null && w === p.alternate) break t;
              w = Dn(w), p = Dn(p);
            }
            w = null;
          }
          else w = null;
          m !== null && zc(c, f, m, w, !1), y !== null && x !== null && zc(c, x, y, w, !0);
        }
      }
      e: {
        if (f = a ? Bn(a) : window, m = f.nodeName && f.nodeName.toLowerCase(), m === "select" || m === "input" && f.type === "file") var S = by;
        else if (Sc(f)) if (ch) S = qy;
        else {
          S = Gy;
          var E = Ky;
        }
        else (m = f.nodeName) && m.toLowerCase() === "input" && (f.type === "checkbox" || f.type === "radio") && (S = Zy);
        if (S && (S = S(e, a))) {
          ah(c, S, n, d);
          break e;
        }
        E && E(e, f, a), e === "focusout" && (E = f._wrapperState) && E.controlled && f.type === "number" && Al(f, "number", f.value);
      }
      switch (E = a ? Bn(a) : window, e) {
        case "focusin":
          (Sc(E) || E.contentEditable === "true") && (Hn = E, Yl = a, br = null);
          break;
        case "focusout":
          br = Yl = Hn = null;
          break;
        case "mousedown":
          Xl = !0;
          break;
        case "contextmenu":
        case "mouseup":
        case "dragend":
          Xl = !1, Cc(c, n, d);
          break;
        case "selectionchange":
          if (tv) break;
        case "keydown":
        case "keyup":
          Cc(c, n, d);
      }
      var M;
      if (qu) e: {
        switch (e) {
          case "compositionstart":
            var P = "onCompositionStart";
            break e;
          case "compositionend":
            P = "onCompositionEnd";
            break e;
          case "compositionupdate":
            P = "onCompositionUpdate";
            break e;
        }
        P = void 0;
      }
      else Fn ? lh(e, n) && (P = "onCompositionEnd") : e === "keydown" && n.keyCode === 229 && (P = "onCompositionStart");
      P && (sh && n.locale !== "ko" && (Fn || P !== "onCompositionStart" ? P === "onCompositionEnd" && Fn && (M = ih()) : (bt = d, Ku = "value" in bt ? bt.value : bt.textContent, Fn = !0)), E = Oi(a, P), 0 < E.length && (P = new yc(P, e, null, n, d), c.push({ event: P, listeners: E }), M ? P.data = M : (M = uh(n), M !== null && (P.data = M)))), (M = Uy ? Wy(e, n) : Yy(e, n)) && (a = Oi(a, "onBeforeInput"), 0 < a.length && (d = new yc("onBeforeInput", "beforeinput", null, n, d), c.push({ event: d, listeners: a }), d.data = M));
    }
    xh(c, t);
  });
}
function co(e, t, n) {
  return { instance: e, listener: t, currentTarget: n };
}
function Oi(e, t) {
  for (var n = t + "Capture", r = []; e !== null; ) {
    var o = e, i = o.stateNode;
    o.tag === 5 && i !== null && (o = i, i = ro(e, n), i != null && r.unshift(co(e, i, o)), i = ro(e, t), i != null && r.push(co(e, i, o))), e = e.return;
  }
  return r;
}
function Dn(e) {
  if (e === null) return null;
  do
    e = e.return;
  while (e && e.tag !== 5);
  return e || null;
}
function zc(e, t, n, r, o) {
  for (var i = t._reactName, s = []; n !== null && n !== r; ) {
    var l = n, u = l.alternate, a = l.stateNode;
    if (u !== null && u === r) break;
    l.tag === 5 && a !== null && (l = a, o ? (u = ro(n, i), u != null && s.unshift(co(n, u, l))) : o || (u = ro(n, i), u != null && s.push(co(n, u, l)))), n = n.return;
  }
  s.length !== 0 && e.push({ event: t, listeners: s });
}
var iv = /\r\n?/g, sv = /\u0000|\uFFFD/g;
function Ic(e) {
  return (typeof e == "string" ? e : "" + e).replace(iv, `
`).replace(sv, "");
}
function qo(e, t, n) {
  if (t = Ic(t), Ic(e) !== t && n) throw Error(V(425));
}
function Fi() {
}
var Ql = null, bl = null;
function Kl(e, t) {
  return e === "textarea" || e === "noscript" || typeof t.children == "string" || typeof t.children == "number" || typeof t.dangerouslySetInnerHTML == "object" && t.dangerouslySetInnerHTML !== null && t.dangerouslySetInnerHTML.__html != null;
}
var Gl = typeof setTimeout == "function" ? setTimeout : void 0, lv = typeof clearTimeout == "function" ? clearTimeout : void 0, Tc = typeof Promise == "function" ? Promise : void 0, uv = typeof queueMicrotask == "function" ? queueMicrotask : typeof Tc < "u" ? function(e) {
  return Tc.resolve(null).then(e).catch(av);
} : Gl;
function av(e) {
  setTimeout(function() {
    throw e;
  });
}
function sl(e, t) {
  var n = t, r = 0;
  do {
    var o = n.nextSibling;
    if (e.removeChild(n), o && o.nodeType === 8) if (n = o.data, n === "/$") {
      if (r === 0) {
        e.removeChild(o), so(t);
        return;
      }
      r--;
    } else n !== "$" && n !== "$?" && n !== "$!" || r++;
    n = o;
  } while (n);
  so(t);
}
function Jt(e) {
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
function Lc(e) {
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
var Er = Math.random().toString(36).slice(2), vt = "__reactFiber$" + Er, fo = "__reactProps$" + Er, At = "__reactContainer$" + Er, Zl = "__reactEvents$" + Er, cv = "__reactListeners$" + Er, fv = "__reactHandles$" + Er;
function gn(e) {
  var t = e[vt];
  if (t) return t;
  for (var n = e.parentNode; n; ) {
    if (t = n[At] || n[vt]) {
      if (n = t.alternate, t.child !== null || n !== null && n.child !== null) for (e = Lc(e); e !== null; ) {
        if (n = e[vt]) return n;
        e = Lc(e);
      }
      return t;
    }
    e = n, n = e.parentNode;
  }
  return null;
}
function Lo(e) {
  return e = e[vt] || e[At], !e || e.tag !== 5 && e.tag !== 6 && e.tag !== 13 && e.tag !== 3 ? null : e;
}
function Bn(e) {
  if (e.tag === 5 || e.tag === 6) return e.stateNode;
  throw Error(V(33));
}
function ms(e) {
  return e[fo] || null;
}
var ql = [], Un = -1;
function an(e) {
  return { current: e };
}
function ce(e) {
  0 > Un || (e.current = ql[Un], ql[Un] = null, Un--);
}
function le(e, t) {
  Un++, ql[Un] = e.current, e.current = t;
}
var ln = {}, Le = an(ln), He = an(!1), En = ln;
function sr(e, t) {
  var n = e.type.contextTypes;
  if (!n) return ln;
  var r = e.stateNode;
  if (r && r.__reactInternalMemoizedUnmaskedChildContext === t) return r.__reactInternalMemoizedMaskedChildContext;
  var o = {}, i;
  for (i in n) o[i] = t[i];
  return r && (e = e.stateNode, e.__reactInternalMemoizedUnmaskedChildContext = t, e.__reactInternalMemoizedMaskedChildContext = o), o;
}
function Ve(e) {
  return e = e.childContextTypes, e != null;
}
function Hi() {
  ce(He), ce(Le);
}
function Ac(e, t, n) {
  if (Le.current !== ln) throw Error(V(168));
  le(Le, t), le(He, n);
}
function _h(e, t, n) {
  var r = e.stateNode;
  if (t = t.childContextTypes, typeof r.getChildContext != "function") return n;
  r = r.getChildContext();
  for (var o in r) if (!(o in t)) throw Error(V(108, K0(e) || "Unknown", o));
  return me({}, n, r);
}
function Vi(e) {
  return e = (e = e.stateNode) && e.__reactInternalMemoizedMergedChildContext || ln, En = Le.current, le(Le, e), le(He, He.current), !0;
}
function $c(e, t, n) {
  var r = e.stateNode;
  if (!r) throw Error(V(169));
  n ? (e = _h(e, t, En), r.__reactInternalMemoizedMergedChildContext = e, ce(He), ce(Le), le(Le, e)) : ce(He), le(He, n);
}
var Mt = null, ys = !1, ll = !1;
function Eh(e) {
  Mt === null ? Mt = [e] : Mt.push(e);
}
function dv(e) {
  ys = !0, Eh(e);
}
function cn() {
  if (!ll && Mt !== null) {
    ll = !0;
    var e = 0, t = se;
    try {
      var n = Mt;
      for (se = 1; e < n.length; e++) {
        var r = n[e];
        do
          r = r(!0);
        while (r !== null);
      }
      Mt = null, ys = !1;
    } catch (o) {
      throw Mt !== null && (Mt = Mt.slice(e + 1)), bd(Yu, cn), o;
    } finally {
      se = t, ll = !1;
    }
  }
  return null;
}
var Wn = [], Yn = 0, Bi = null, Ui = 0, Ze = [], qe = 0, kn = null, Pt = 1, zt = "";
function dn(e, t) {
  Wn[Yn++] = Ui, Wn[Yn++] = Bi, Bi = e, Ui = t;
}
function kh(e, t, n) {
  Ze[qe++] = Pt, Ze[qe++] = zt, Ze[qe++] = kn, kn = e;
  var r = Pt;
  e = zt;
  var o = 32 - ft(r) - 1;
  r &= ~(1 << o), n += 1;
  var i = 32 - ft(t) + o;
  if (30 < i) {
    var s = o - o % 5;
    i = (r & (1 << s) - 1).toString(32), r >>= s, o -= s, Pt = 1 << 32 - ft(t) + o | n << o | r, zt = i + e;
  } else Pt = 1 << i | n << o | r, zt = e;
}
function ea(e) {
  e.return !== null && (dn(e, 1), kh(e, 1, 0));
}
function ta(e) {
  for (; e === Bi; ) Bi = Wn[--Yn], Wn[Yn] = null, Ui = Wn[--Yn], Wn[Yn] = null;
  for (; e === kn; ) kn = Ze[--qe], Ze[qe] = null, zt = Ze[--qe], Ze[qe] = null, Pt = Ze[--qe], Ze[qe] = null;
}
var Xe = null, Ye = null, fe = !1, lt = null;
function Nh(e, t) {
  var n = Je(5, null, null, 0);
  n.elementType = "DELETED", n.stateNode = t, n.return = e, t = e.deletions, t === null ? (e.deletions = [n], e.flags |= 16) : t.push(n);
}
function Dc(e, t) {
  switch (e.tag) {
    case 5:
      var n = e.type;
      return t = t.nodeType !== 1 || n.toLowerCase() !== t.nodeName.toLowerCase() ? null : t, t !== null ? (e.stateNode = t, Xe = e, Ye = Jt(t.firstChild), !0) : !1;
    case 6:
      return t = e.pendingProps === "" || t.nodeType !== 3 ? null : t, t !== null ? (e.stateNode = t, Xe = e, Ye = null, !0) : !1;
    case 13:
      return t = t.nodeType !== 8 ? null : t, t !== null ? (n = kn !== null ? { id: Pt, overflow: zt } : null, e.memoizedState = { dehydrated: t, treeContext: n, retryLane: 1073741824 }, n = Je(18, null, null, 0), n.stateNode = t, n.return = e, e.child = n, Xe = e, Ye = null, !0) : !1;
    default:
      return !1;
  }
}
function Jl(e) {
  return (e.mode & 1) !== 0 && (e.flags & 128) === 0;
}
function eu(e) {
  if (fe) {
    var t = Ye;
    if (t) {
      var n = t;
      if (!Dc(e, t)) {
        if (Jl(e)) throw Error(V(418));
        t = Jt(n.nextSibling);
        var r = Xe;
        t && Dc(e, t) ? Nh(r, n) : (e.flags = e.flags & -4097 | 2, fe = !1, Xe = e);
      }
    } else {
      if (Jl(e)) throw Error(V(418));
      e.flags = e.flags & -4097 | 2, fe = !1, Xe = e;
    }
  }
}
function Rc(e) {
  for (e = e.return; e !== null && e.tag !== 5 && e.tag !== 3 && e.tag !== 13; ) e = e.return;
  Xe = e;
}
function Jo(e) {
  if (e !== Xe) return !1;
  if (!fe) return Rc(e), fe = !0, !1;
  var t;
  if ((t = e.tag !== 3) && !(t = e.tag !== 5) && (t = e.type, t = t !== "head" && t !== "body" && !Kl(e.type, e.memoizedProps)), t && (t = Ye)) {
    if (Jl(e)) throw Ch(), Error(V(418));
    for (; t; ) Nh(e, t), t = Jt(t.nextSibling);
  }
  if (Rc(e), e.tag === 13) {
    if (e = e.memoizedState, e = e !== null ? e.dehydrated : null, !e) throw Error(V(317));
    e: {
      for (e = e.nextSibling, t = 0; e; ) {
        if (e.nodeType === 8) {
          var n = e.data;
          if (n === "/$") {
            if (t === 0) {
              Ye = Jt(e.nextSibling);
              break e;
            }
            t--;
          } else n !== "$" && n !== "$!" && n !== "$?" || t++;
        }
        e = e.nextSibling;
      }
      Ye = null;
    }
  } else Ye = Xe ? Jt(e.stateNode.nextSibling) : null;
  return !0;
}
function Ch() {
  for (var e = Ye; e; ) e = Jt(e.nextSibling);
}
function lr() {
  Ye = Xe = null, fe = !1;
}
function na(e) {
  lt === null ? lt = [e] : lt.push(e);
}
var hv = jt.ReactCurrentBatchConfig;
function Ar(e, t, n) {
  if (e = n.ref, e !== null && typeof e != "function" && typeof e != "object") {
    if (n._owner) {
      if (n = n._owner, n) {
        if (n.tag !== 1) throw Error(V(309));
        var r = n.stateNode;
      }
      if (!r) throw Error(V(147, e));
      var o = r, i = "" + e;
      return t !== null && t.ref !== null && typeof t.ref == "function" && t.ref._stringRef === i ? t.ref : (t = function(s) {
        var l = o.refs;
        s === null ? delete l[i] : l[i] = s;
      }, t._stringRef = i, t);
    }
    if (typeof e != "string") throw Error(V(284));
    if (!n._owner) throw Error(V(290, e));
  }
  return e;
}
function ei(e, t) {
  throw e = Object.prototype.toString.call(t), Error(V(31, e === "[object Object]" ? "object with keys {" + Object.keys(t).join(", ") + "}" : e));
}
function jc(e) {
  var t = e._init;
  return t(e._payload);
}
function Mh(e) {
  function t(p, g) {
    if (e) {
      var h = p.deletions;
      h === null ? (p.deletions = [g], p.flags |= 16) : h.push(g);
    }
  }
  function n(p, g) {
    if (!e) return null;
    for (; g !== null; ) t(p, g), g = g.sibling;
    return null;
  }
  function r(p, g) {
    for (p = /* @__PURE__ */ new Map(); g !== null; ) g.key !== null ? p.set(g.key, g) : p.set(g.index, g), g = g.sibling;
    return p;
  }
  function o(p, g) {
    return p = rn(p, g), p.index = 0, p.sibling = null, p;
  }
  function i(p, g, h) {
    return p.index = h, e ? (h = p.alternate, h !== null ? (h = h.index, h < g ? (p.flags |= 2, g) : h) : (p.flags |= 2, g)) : (p.flags |= 1048576, g);
  }
  function s(p) {
    return e && p.alternate === null && (p.flags |= 2), p;
  }
  function l(p, g, h, v) {
    return g === null || g.tag !== 6 ? (g = pl(h, p.mode, v), g.return = p, g) : (g = o(g, h), g.return = p, g);
  }
  function u(p, g, h, v) {
    var S = h.type;
    return S === On ? d(p, g, h.props.children, v, h.key) : g !== null && (g.elementType === S || typeof S == "object" && S !== null && S.$$typeof === Ut && jc(S) === g.type) ? (v = o(g, h.props), v.ref = Ar(p, g, h), v.return = p, v) : (v = Ni(h.type, h.key, h.props, null, p.mode, v), v.ref = Ar(p, g, h), v.return = p, v);
  }
  function a(p, g, h, v) {
    return g === null || g.tag !== 4 || g.stateNode.containerInfo !== h.containerInfo || g.stateNode.implementation !== h.implementation ? (g = gl(h, p.mode, v), g.return = p, g) : (g = o(g, h.children || []), g.return = p, g);
  }
  function d(p, g, h, v, S) {
    return g === null || g.tag !== 7 ? (g = xn(h, p.mode, v, S), g.return = p, g) : (g = o(g, h), g.return = p, g);
  }
  function c(p, g, h) {
    if (typeof g == "string" && g !== "" || typeof g == "number") return g = pl("" + g, p.mode, h), g.return = p, g;
    if (typeof g == "object" && g !== null) {
      switch (g.$$typeof) {
        case Uo:
          return h = Ni(g.type, g.key, g.props, null, p.mode, h), h.ref = Ar(p, null, g), h.return = p, h;
        case jn:
          return g = gl(g, p.mode, h), g.return = p, g;
        case Ut:
          var v = g._init;
          return c(p, v(g._payload), h);
      }
      if (Fr(g) || Pr(g)) return g = xn(g, p.mode, h, null), g.return = p, g;
      ei(p, g);
    }
    return null;
  }
  function f(p, g, h, v) {
    var S = g !== null ? g.key : null;
    if (typeof h == "string" && h !== "" || typeof h == "number") return S !== null ? null : l(p, g, "" + h, v);
    if (typeof h == "object" && h !== null) {
      switch (h.$$typeof) {
        case Uo:
          return h.key === S ? u(p, g, h, v) : null;
        case jn:
          return h.key === S ? a(p, g, h, v) : null;
        case Ut:
          return S = h._init, f(
            p,
            g,
            S(h._payload),
            v
          );
      }
      if (Fr(h) || Pr(h)) return S !== null ? null : d(p, g, h, v, null);
      ei(p, h);
    }
    return null;
  }
  function m(p, g, h, v, S) {
    if (typeof v == "string" && v !== "" || typeof v == "number") return p = p.get(h) || null, l(g, p, "" + v, S);
    if (typeof v == "object" && v !== null) {
      switch (v.$$typeof) {
        case Uo:
          return p = p.get(v.key === null ? h : v.key) || null, u(g, p, v, S);
        case jn:
          return p = p.get(v.key === null ? h : v.key) || null, a(g, p, v, S);
        case Ut:
          var E = v._init;
          return m(p, g, h, E(v._payload), S);
      }
      if (Fr(v) || Pr(v)) return p = p.get(h) || null, d(g, p, v, S, null);
      ei(g, v);
    }
    return null;
  }
  function y(p, g, h, v) {
    for (var S = null, E = null, M = g, P = g = 0, T = null; M !== null && P < h.length; P++) {
      M.index > P ? (T = M, M = null) : T = M.sibling;
      var j = f(p, M, h[P], v);
      if (j === null) {
        M === null && (M = T);
        break;
      }
      e && M && j.alternate === null && t(p, M), g = i(j, g, P), E === null ? S = j : E.sibling = j, E = j, M = T;
    }
    if (P === h.length) return n(p, M), fe && dn(p, P), S;
    if (M === null) {
      for (; P < h.length; P++) M = c(p, h[P], v), M !== null && (g = i(M, g, P), E === null ? S = M : E.sibling = M, E = M);
      return fe && dn(p, P), S;
    }
    for (M = r(p, M); P < h.length; P++) T = m(M, p, P, h[P], v), T !== null && (e && T.alternate !== null && M.delete(T.key === null ? P : T.key), g = i(T, g, P), E === null ? S = T : E.sibling = T, E = T);
    return e && M.forEach(function(I) {
      return t(p, I);
    }), fe && dn(p, P), S;
  }
  function w(p, g, h, v) {
    var S = Pr(h);
    if (typeof S != "function") throw Error(V(150));
    if (h = S.call(h), h == null) throw Error(V(151));
    for (var E = S = null, M = g, P = g = 0, T = null, j = h.next(); M !== null && !j.done; P++, j = h.next()) {
      M.index > P ? (T = M, M = null) : T = M.sibling;
      var I = f(p, M, j.value, v);
      if (I === null) {
        M === null && (M = T);
        break;
      }
      e && M && I.alternate === null && t(p, M), g = i(I, g, P), E === null ? S = I : E.sibling = I, E = I, M = T;
    }
    if (j.done) return n(
      p,
      M
    ), fe && dn(p, P), S;
    if (M === null) {
      for (; !j.done; P++, j = h.next()) j = c(p, j.value, v), j !== null && (g = i(j, g, P), E === null ? S = j : E.sibling = j, E = j);
      return fe && dn(p, P), S;
    }
    for (M = r(p, M); !j.done; P++, j = h.next()) j = m(M, p, P, j.value, v), j !== null && (e && j.alternate !== null && M.delete(j.key === null ? P : j.key), g = i(j, g, P), E === null ? S = j : E.sibling = j, E = j);
    return e && M.forEach(function(D) {
      return t(p, D);
    }), fe && dn(p, P), S;
  }
  function x(p, g, h, v) {
    if (typeof h == "object" && h !== null && h.type === On && h.key === null && (h = h.props.children), typeof h == "object" && h !== null) {
      switch (h.$$typeof) {
        case Uo:
          e: {
            for (var S = h.key, E = g; E !== null; ) {
              if (E.key === S) {
                if (S = h.type, S === On) {
                  if (E.tag === 7) {
                    n(p, E.sibling), g = o(E, h.props.children), g.return = p, p = g;
                    break e;
                  }
                } else if (E.elementType === S || typeof S == "object" && S !== null && S.$$typeof === Ut && jc(S) === E.type) {
                  n(p, E.sibling), g = o(E, h.props), g.ref = Ar(p, E, h), g.return = p, p = g;
                  break e;
                }
                n(p, E);
                break;
              } else t(p, E);
              E = E.sibling;
            }
            h.type === On ? (g = xn(h.props.children, p.mode, v, h.key), g.return = p, p = g) : (v = Ni(h.type, h.key, h.props, null, p.mode, v), v.ref = Ar(p, g, h), v.return = p, p = v);
          }
          return s(p);
        case jn:
          e: {
            for (E = h.key; g !== null; ) {
              if (g.key === E) if (g.tag === 4 && g.stateNode.containerInfo === h.containerInfo && g.stateNode.implementation === h.implementation) {
                n(p, g.sibling), g = o(g, h.children || []), g.return = p, p = g;
                break e;
              } else {
                n(p, g);
                break;
              }
              else t(p, g);
              g = g.sibling;
            }
            g = gl(h, p.mode, v), g.return = p, p = g;
          }
          return s(p);
        case Ut:
          return E = h._init, x(p, g, E(h._payload), v);
      }
      if (Fr(h)) return y(p, g, h, v);
      if (Pr(h)) return w(p, g, h, v);
      ei(p, h);
    }
    return typeof h == "string" && h !== "" || typeof h == "number" ? (h = "" + h, g !== null && g.tag === 6 ? (n(p, g.sibling), g = o(g, h), g.return = p, p = g) : (n(p, g), g = pl(h, p.mode, v), g.return = p, p = g), s(p)) : n(p, g);
  }
  return x;
}
var ur = Mh(!0), Ph = Mh(!1), Wi = an(null), Yi = null, Xn = null, ra = null;
function oa() {
  ra = Xn = Yi = null;
}
function ia(e) {
  var t = Wi.current;
  ce(Wi), e._currentValue = t;
}
function tu(e, t, n) {
  for (; e !== null; ) {
    var r = e.alternate;
    if ((e.childLanes & t) !== t ? (e.childLanes |= t, r !== null && (r.childLanes |= t)) : r !== null && (r.childLanes & t) !== t && (r.childLanes |= t), e === n) break;
    e = e.return;
  }
}
function tr(e, t) {
  Yi = e, ra = Xn = null, e = e.dependencies, e !== null && e.firstContext !== null && (e.lanes & t && (Oe = !0), e.firstContext = null);
}
function tt(e) {
  var t = e._currentValue;
  if (ra !== e) if (e = { context: e, memoizedValue: t, next: null }, Xn === null) {
    if (Yi === null) throw Error(V(308));
    Xn = e, Yi.dependencies = { lanes: 0, firstContext: e };
  } else Xn = Xn.next = e;
  return t;
}
var mn = null;
function sa(e) {
  mn === null ? mn = [e] : mn.push(e);
}
function zh(e, t, n, r) {
  var o = t.interleaved;
  return o === null ? (n.next = n, sa(t)) : (n.next = o.next, o.next = n), t.interleaved = n, $t(e, r);
}
function $t(e, t) {
  e.lanes |= t;
  var n = e.alternate;
  for (n !== null && (n.lanes |= t), n = e, e = e.return; e !== null; ) e.childLanes |= t, n = e.alternate, n !== null && (n.childLanes |= t), n = e, e = e.return;
  return n.tag === 3 ? n.stateNode : null;
}
var Wt = !1;
function la(e) {
  e.updateQueue = { baseState: e.memoizedState, firstBaseUpdate: null, lastBaseUpdate: null, shared: { pending: null, interleaved: null, lanes: 0 }, effects: null };
}
function Ih(e, t) {
  e = e.updateQueue, t.updateQueue === e && (t.updateQueue = { baseState: e.baseState, firstBaseUpdate: e.firstBaseUpdate, lastBaseUpdate: e.lastBaseUpdate, shared: e.shared, effects: e.effects });
}
function Tt(e, t) {
  return { eventTime: e, lane: t, tag: 0, payload: null, callback: null, next: null };
}
function en(e, t, n) {
  var r = e.updateQueue;
  if (r === null) return null;
  if (r = r.shared, re & 2) {
    var o = r.pending;
    return o === null ? t.next = t : (t.next = o.next, o.next = t), r.pending = t, $t(e, n);
  }
  return o = r.interleaved, o === null ? (t.next = t, sa(r)) : (t.next = o.next, o.next = t), r.interleaved = t, $t(e, n);
}
function wi(e, t, n) {
  if (t = t.updateQueue, t !== null && (t = t.shared, (n & 4194240) !== 0)) {
    var r = t.lanes;
    r &= e.pendingLanes, n |= r, t.lanes = n, Xu(e, n);
  }
}
function Oc(e, t) {
  var n = e.updateQueue, r = e.alternate;
  if (r !== null && (r = r.updateQueue, n === r)) {
    var o = null, i = null;
    if (n = n.firstBaseUpdate, n !== null) {
      do {
        var s = { eventTime: n.eventTime, lane: n.lane, tag: n.tag, payload: n.payload, callback: n.callback, next: null };
        i === null ? o = i = s : i = i.next = s, n = n.next;
      } while (n !== null);
      i === null ? o = i = t : i = i.next = t;
    } else o = i = t;
    n = { baseState: r.baseState, firstBaseUpdate: o, lastBaseUpdate: i, shared: r.shared, effects: r.effects }, e.updateQueue = n;
    return;
  }
  e = n.lastBaseUpdate, e === null ? n.firstBaseUpdate = t : e.next = t, n.lastBaseUpdate = t;
}
function Xi(e, t, n, r) {
  var o = e.updateQueue;
  Wt = !1;
  var i = o.firstBaseUpdate, s = o.lastBaseUpdate, l = o.shared.pending;
  if (l !== null) {
    o.shared.pending = null;
    var u = l, a = u.next;
    u.next = null, s === null ? i = a : s.next = a, s = u;
    var d = e.alternate;
    d !== null && (d = d.updateQueue, l = d.lastBaseUpdate, l !== s && (l === null ? d.firstBaseUpdate = a : l.next = a, d.lastBaseUpdate = u));
  }
  if (i !== null) {
    var c = o.baseState;
    s = 0, d = a = u = null, l = i;
    do {
      var f = l.lane, m = l.eventTime;
      if ((r & f) === f) {
        d !== null && (d = d.next = {
          eventTime: m,
          lane: 0,
          tag: l.tag,
          payload: l.payload,
          callback: l.callback,
          next: null
        });
        e: {
          var y = e, w = l;
          switch (f = t, m = n, w.tag) {
            case 1:
              if (y = w.payload, typeof y == "function") {
                c = y.call(m, c, f);
                break e;
              }
              c = y;
              break e;
            case 3:
              y.flags = y.flags & -65537 | 128;
            case 0:
              if (y = w.payload, f = typeof y == "function" ? y.call(m, c, f) : y, f == null) break e;
              c = me({}, c, f);
              break e;
            case 2:
              Wt = !0;
          }
        }
        l.callback !== null && l.lane !== 0 && (e.flags |= 64, f = o.effects, f === null ? o.effects = [l] : f.push(l));
      } else m = { eventTime: m, lane: f, tag: l.tag, payload: l.payload, callback: l.callback, next: null }, d === null ? (a = d = m, u = c) : d = d.next = m, s |= f;
      if (l = l.next, l === null) {
        if (l = o.shared.pending, l === null) break;
        f = l, l = f.next, f.next = null, o.lastBaseUpdate = f, o.shared.pending = null;
      }
    } while (!0);
    if (d === null && (u = c), o.baseState = u, o.firstBaseUpdate = a, o.lastBaseUpdate = d, t = o.shared.interleaved, t !== null) {
      o = t;
      do
        s |= o.lane, o = o.next;
      while (o !== t);
    } else i === null && (o.shared.lanes = 0);
    Cn |= s, e.lanes = s, e.memoizedState = c;
  }
}
function Fc(e, t, n) {
  if (e = t.effects, t.effects = null, e !== null) for (t = 0; t < e.length; t++) {
    var r = e[t], o = r.callback;
    if (o !== null) {
      if (r.callback = null, r = n, typeof o != "function") throw Error(V(191, o));
      o.call(r);
    }
  }
}
var Ao = {}, xt = an(Ao), ho = an(Ao), po = an(Ao);
function yn(e) {
  if (e === Ao) throw Error(V(174));
  return e;
}
function ua(e, t) {
  switch (le(po, t), le(ho, e), le(xt, Ao), e = t.nodeType, e) {
    case 9:
    case 11:
      t = (t = t.documentElement) ? t.namespaceURI : Dl(null, "");
      break;
    default:
      e = e === 8 ? t.parentNode : t, t = e.namespaceURI || null, e = e.tagName, t = Dl(t, e);
  }
  ce(xt), le(xt, t);
}
function ar() {
  ce(xt), ce(ho), ce(po);
}
function Th(e) {
  yn(po.current);
  var t = yn(xt.current), n = Dl(t, e.type);
  t !== n && (le(ho, e), le(xt, n));
}
function aa(e) {
  ho.current === e && (ce(xt), ce(ho));
}
var pe = an(0);
function Qi(e) {
  for (var t = e; t !== null; ) {
    if (t.tag === 13) {
      var n = t.memoizedState;
      if (n !== null && (n = n.dehydrated, n === null || n.data === "$?" || n.data === "$!")) return t;
    } else if (t.tag === 19 && t.memoizedProps.revealOrder !== void 0) {
      if (t.flags & 128) return t;
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
var ul = [];
function ca() {
  for (var e = 0; e < ul.length; e++) ul[e]._workInProgressVersionPrimary = null;
  ul.length = 0;
}
var xi = jt.ReactCurrentDispatcher, al = jt.ReactCurrentBatchConfig, Nn = 0, ge = null, Se = null, Ee = null, bi = !1, Kr = !1, go = 0, pv = 0;
function ze() {
  throw Error(V(321));
}
function fa(e, t) {
  if (t === null) return !1;
  for (var n = 0; n < t.length && n < e.length; n++) if (!ht(e[n], t[n])) return !1;
  return !0;
}
function da(e, t, n, r, o, i) {
  if (Nn = i, ge = t, t.memoizedState = null, t.updateQueue = null, t.lanes = 0, xi.current = e === null || e.memoizedState === null ? vv : wv, e = n(r, o), Kr) {
    i = 0;
    do {
      if (Kr = !1, go = 0, 25 <= i) throw Error(V(301));
      i += 1, Ee = Se = null, t.updateQueue = null, xi.current = xv, e = n(r, o);
    } while (Kr);
  }
  if (xi.current = Ki, t = Se !== null && Se.next !== null, Nn = 0, Ee = Se = ge = null, bi = !1, t) throw Error(V(300));
  return e;
}
function ha() {
  var e = go !== 0;
  return go = 0, e;
}
function mt() {
  var e = { memoizedState: null, baseState: null, baseQueue: null, queue: null, next: null };
  return Ee === null ? ge.memoizedState = Ee = e : Ee = Ee.next = e, Ee;
}
function nt() {
  if (Se === null) {
    var e = ge.alternate;
    e = e !== null ? e.memoizedState : null;
  } else e = Se.next;
  var t = Ee === null ? ge.memoizedState : Ee.next;
  if (t !== null) Ee = t, Se = e;
  else {
    if (e === null) throw Error(V(310));
    Se = e, e = { memoizedState: Se.memoizedState, baseState: Se.baseState, baseQueue: Se.baseQueue, queue: Se.queue, next: null }, Ee === null ? ge.memoizedState = Ee = e : Ee = Ee.next = e;
  }
  return Ee;
}
function mo(e, t) {
  return typeof t == "function" ? t(e) : t;
}
function cl(e) {
  var t = nt(), n = t.queue;
  if (n === null) throw Error(V(311));
  n.lastRenderedReducer = e;
  var r = Se, o = r.baseQueue, i = n.pending;
  if (i !== null) {
    if (o !== null) {
      var s = o.next;
      o.next = i.next, i.next = s;
    }
    r.baseQueue = o = i, n.pending = null;
  }
  if (o !== null) {
    i = o.next, r = r.baseState;
    var l = s = null, u = null, a = i;
    do {
      var d = a.lane;
      if ((Nn & d) === d) u !== null && (u = u.next = { lane: 0, action: a.action, hasEagerState: a.hasEagerState, eagerState: a.eagerState, next: null }), r = a.hasEagerState ? a.eagerState : e(r, a.action);
      else {
        var c = {
          lane: d,
          action: a.action,
          hasEagerState: a.hasEagerState,
          eagerState: a.eagerState,
          next: null
        };
        u === null ? (l = u = c, s = r) : u = u.next = c, ge.lanes |= d, Cn |= d;
      }
      a = a.next;
    } while (a !== null && a !== i);
    u === null ? s = r : u.next = l, ht(r, t.memoizedState) || (Oe = !0), t.memoizedState = r, t.baseState = s, t.baseQueue = u, n.lastRenderedState = r;
  }
  if (e = n.interleaved, e !== null) {
    o = e;
    do
      i = o.lane, ge.lanes |= i, Cn |= i, o = o.next;
    while (o !== e);
  } else o === null && (n.lanes = 0);
  return [t.memoizedState, n.dispatch];
}
function fl(e) {
  var t = nt(), n = t.queue;
  if (n === null) throw Error(V(311));
  n.lastRenderedReducer = e;
  var r = n.dispatch, o = n.pending, i = t.memoizedState;
  if (o !== null) {
    n.pending = null;
    var s = o = o.next;
    do
      i = e(i, s.action), s = s.next;
    while (s !== o);
    ht(i, t.memoizedState) || (Oe = !0), t.memoizedState = i, t.baseQueue === null && (t.baseState = i), n.lastRenderedState = i;
  }
  return [i, r];
}
function Lh() {
}
function Ah(e, t) {
  var n = ge, r = nt(), o = t(), i = !ht(r.memoizedState, o);
  if (i && (r.memoizedState = o, Oe = !0), r = r.queue, pa(Rh.bind(null, n, r, e), [e]), r.getSnapshot !== t || i || Ee !== null && Ee.memoizedState.tag & 1) {
    if (n.flags |= 2048, yo(9, Dh.bind(null, n, r, o, t), void 0, null), ke === null) throw Error(V(349));
    Nn & 30 || $h(n, t, o);
  }
  return o;
}
function $h(e, t, n) {
  e.flags |= 16384, e = { getSnapshot: t, value: n }, t = ge.updateQueue, t === null ? (t = { lastEffect: null, stores: null }, ge.updateQueue = t, t.stores = [e]) : (n = t.stores, n === null ? t.stores = [e] : n.push(e));
}
function Dh(e, t, n, r) {
  t.value = n, t.getSnapshot = r, jh(t) && Oh(e);
}
function Rh(e, t, n) {
  return n(function() {
    jh(t) && Oh(e);
  });
}
function jh(e) {
  var t = e.getSnapshot;
  e = e.value;
  try {
    var n = t();
    return !ht(e, n);
  } catch {
    return !0;
  }
}
function Oh(e) {
  var t = $t(e, 1);
  t !== null && dt(t, e, 1, -1);
}
function Hc(e) {
  var t = mt();
  return typeof e == "function" && (e = e()), t.memoizedState = t.baseState = e, e = { pending: null, interleaved: null, lanes: 0, dispatch: null, lastRenderedReducer: mo, lastRenderedState: e }, t.queue = e, e = e.dispatch = yv.bind(null, ge, e), [t.memoizedState, e];
}
function yo(e, t, n, r) {
  return e = { tag: e, create: t, destroy: n, deps: r, next: null }, t = ge.updateQueue, t === null ? (t = { lastEffect: null, stores: null }, ge.updateQueue = t, t.lastEffect = e.next = e) : (n = t.lastEffect, n === null ? t.lastEffect = e.next = e : (r = n.next, n.next = e, e.next = r, t.lastEffect = e)), e;
}
function Fh() {
  return nt().memoizedState;
}
function Si(e, t, n, r) {
  var o = mt();
  ge.flags |= e, o.memoizedState = yo(1 | t, n, void 0, r === void 0 ? null : r);
}
function vs(e, t, n, r) {
  var o = nt();
  r = r === void 0 ? null : r;
  var i = void 0;
  if (Se !== null) {
    var s = Se.memoizedState;
    if (i = s.destroy, r !== null && fa(r, s.deps)) {
      o.memoizedState = yo(t, n, i, r);
      return;
    }
  }
  ge.flags |= e, o.memoizedState = yo(1 | t, n, i, r);
}
function Vc(e, t) {
  return Si(8390656, 8, e, t);
}
function pa(e, t) {
  return vs(2048, 8, e, t);
}
function Hh(e, t) {
  return vs(4, 2, e, t);
}
function Vh(e, t) {
  return vs(4, 4, e, t);
}
function Bh(e, t) {
  if (typeof t == "function") return e = e(), t(e), function() {
    t(null);
  };
  if (t != null) return e = e(), t.current = e, function() {
    t.current = null;
  };
}
function Uh(e, t, n) {
  return n = n != null ? n.concat([e]) : null, vs(4, 4, Bh.bind(null, t, e), n);
}
function ga() {
}
function Wh(e, t) {
  var n = nt();
  t = t === void 0 ? null : t;
  var r = n.memoizedState;
  return r !== null && t !== null && fa(t, r[1]) ? r[0] : (n.memoizedState = [e, t], e);
}
function Yh(e, t) {
  var n = nt();
  t = t === void 0 ? null : t;
  var r = n.memoizedState;
  return r !== null && t !== null && fa(t, r[1]) ? r[0] : (e = e(), n.memoizedState = [e, t], e);
}
function Xh(e, t, n) {
  return Nn & 21 ? (ht(n, t) || (n = Zd(), ge.lanes |= n, Cn |= n, e.baseState = !0), t) : (e.baseState && (e.baseState = !1, Oe = !0), e.memoizedState = n);
}
function gv(e, t) {
  var n = se;
  se = n !== 0 && 4 > n ? n : 4, e(!0);
  var r = al.transition;
  al.transition = {};
  try {
    e(!1), t();
  } finally {
    se = n, al.transition = r;
  }
}
function Qh() {
  return nt().memoizedState;
}
function mv(e, t, n) {
  var r = nn(e);
  if (n = { lane: r, action: n, hasEagerState: !1, eagerState: null, next: null }, bh(e)) Kh(t, n);
  else if (n = zh(e, t, n, r), n !== null) {
    var o = $e();
    dt(n, e, r, o), Gh(n, t, r);
  }
}
function yv(e, t, n) {
  var r = nn(e), o = { lane: r, action: n, hasEagerState: !1, eagerState: null, next: null };
  if (bh(e)) Kh(t, o);
  else {
    var i = e.alternate;
    if (e.lanes === 0 && (i === null || i.lanes === 0) && (i = t.lastRenderedReducer, i !== null)) try {
      var s = t.lastRenderedState, l = i(s, n);
      if (o.hasEagerState = !0, o.eagerState = l, ht(l, s)) {
        var u = t.interleaved;
        u === null ? (o.next = o, sa(t)) : (o.next = u.next, u.next = o), t.interleaved = o;
        return;
      }
    } catch {
    } finally {
    }
    n = zh(e, t, o, r), n !== null && (o = $e(), dt(n, e, r, o), Gh(n, t, r));
  }
}
function bh(e) {
  var t = e.alternate;
  return e === ge || t !== null && t === ge;
}
function Kh(e, t) {
  Kr = bi = !0;
  var n = e.pending;
  n === null ? t.next = t : (t.next = n.next, n.next = t), e.pending = t;
}
function Gh(e, t, n) {
  if (n & 4194240) {
    var r = t.lanes;
    r &= e.pendingLanes, n |= r, t.lanes = n, Xu(e, n);
  }
}
var Ki = { readContext: tt, useCallback: ze, useContext: ze, useEffect: ze, useImperativeHandle: ze, useInsertionEffect: ze, useLayoutEffect: ze, useMemo: ze, useReducer: ze, useRef: ze, useState: ze, useDebugValue: ze, useDeferredValue: ze, useTransition: ze, useMutableSource: ze, useSyncExternalStore: ze, useId: ze, unstable_isNewReconciler: !1 }, vv = { readContext: tt, useCallback: function(e, t) {
  return mt().memoizedState = [e, t === void 0 ? null : t], e;
}, useContext: tt, useEffect: Vc, useImperativeHandle: function(e, t, n) {
  return n = n != null ? n.concat([e]) : null, Si(
    4194308,
    4,
    Bh.bind(null, t, e),
    n
  );
}, useLayoutEffect: function(e, t) {
  return Si(4194308, 4, e, t);
}, useInsertionEffect: function(e, t) {
  return Si(4, 2, e, t);
}, useMemo: function(e, t) {
  var n = mt();
  return t = t === void 0 ? null : t, e = e(), n.memoizedState = [e, t], e;
}, useReducer: function(e, t, n) {
  var r = mt();
  return t = n !== void 0 ? n(t) : t, r.memoizedState = r.baseState = t, e = { pending: null, interleaved: null, lanes: 0, dispatch: null, lastRenderedReducer: e, lastRenderedState: t }, r.queue = e, e = e.dispatch = mv.bind(null, ge, e), [r.memoizedState, e];
}, useRef: function(e) {
  var t = mt();
  return e = { current: e }, t.memoizedState = e;
}, useState: Hc, useDebugValue: ga, useDeferredValue: function(e) {
  return mt().memoizedState = e;
}, useTransition: function() {
  var e = Hc(!1), t = e[0];
  return e = gv.bind(null, e[1]), mt().memoizedState = e, [t, e];
}, useMutableSource: function() {
}, useSyncExternalStore: function(e, t, n) {
  var r = ge, o = mt();
  if (fe) {
    if (n === void 0) throw Error(V(407));
    n = n();
  } else {
    if (n = t(), ke === null) throw Error(V(349));
    Nn & 30 || $h(r, t, n);
  }
  o.memoizedState = n;
  var i = { value: n, getSnapshot: t };
  return o.queue = i, Vc(Rh.bind(
    null,
    r,
    i,
    e
  ), [e]), r.flags |= 2048, yo(9, Dh.bind(null, r, i, n, t), void 0, null), n;
}, useId: function() {
  var e = mt(), t = ke.identifierPrefix;
  if (fe) {
    var n = zt, r = Pt;
    n = (r & ~(1 << 32 - ft(r) - 1)).toString(32) + n, t = ":" + t + "R" + n, n = go++, 0 < n && (t += "H" + n.toString(32)), t += ":";
  } else n = pv++, t = ":" + t + "r" + n.toString(32) + ":";
  return e.memoizedState = t;
}, unstable_isNewReconciler: !1 }, wv = {
  readContext: tt,
  useCallback: Wh,
  useContext: tt,
  useEffect: pa,
  useImperativeHandle: Uh,
  useInsertionEffect: Hh,
  useLayoutEffect: Vh,
  useMemo: Yh,
  useReducer: cl,
  useRef: Fh,
  useState: function() {
    return cl(mo);
  },
  useDebugValue: ga,
  useDeferredValue: function(e) {
    var t = nt();
    return Xh(t, Se.memoizedState, e);
  },
  useTransition: function() {
    var e = cl(mo)[0], t = nt().memoizedState;
    return [e, t];
  },
  useMutableSource: Lh,
  useSyncExternalStore: Ah,
  useId: Qh,
  unstable_isNewReconciler: !1
}, xv = { readContext: tt, useCallback: Wh, useContext: tt, useEffect: pa, useImperativeHandle: Uh, useInsertionEffect: Hh, useLayoutEffect: Vh, useMemo: Yh, useReducer: fl, useRef: Fh, useState: function() {
  return fl(mo);
}, useDebugValue: ga, useDeferredValue: function(e) {
  var t = nt();
  return Se === null ? t.memoizedState = e : Xh(t, Se.memoizedState, e);
}, useTransition: function() {
  var e = fl(mo)[0], t = nt().memoizedState;
  return [e, t];
}, useMutableSource: Lh, useSyncExternalStore: Ah, useId: Qh, unstable_isNewReconciler: !1 };
function ot(e, t) {
  if (e && e.defaultProps) {
    t = me({}, t), e = e.defaultProps;
    for (var n in e) t[n] === void 0 && (t[n] = e[n]);
    return t;
  }
  return t;
}
function nu(e, t, n, r) {
  t = e.memoizedState, n = n(r, t), n = n == null ? t : me({}, t, n), e.memoizedState = n, e.lanes === 0 && (e.updateQueue.baseState = n);
}
var ws = { isMounted: function(e) {
  return (e = e._reactInternals) ? An(e) === e : !1;
}, enqueueSetState: function(e, t, n) {
  e = e._reactInternals;
  var r = $e(), o = nn(e), i = Tt(r, o);
  i.payload = t, n != null && (i.callback = n), t = en(e, i, o), t !== null && (dt(t, e, o, r), wi(t, e, o));
}, enqueueReplaceState: function(e, t, n) {
  e = e._reactInternals;
  var r = $e(), o = nn(e), i = Tt(r, o);
  i.tag = 1, i.payload = t, n != null && (i.callback = n), t = en(e, i, o), t !== null && (dt(t, e, o, r), wi(t, e, o));
}, enqueueForceUpdate: function(e, t) {
  e = e._reactInternals;
  var n = $e(), r = nn(e), o = Tt(n, r);
  o.tag = 2, t != null && (o.callback = t), t = en(e, o, r), t !== null && (dt(t, e, r, n), wi(t, e, r));
} };
function Bc(e, t, n, r, o, i, s) {
  return e = e.stateNode, typeof e.shouldComponentUpdate == "function" ? e.shouldComponentUpdate(r, i, s) : t.prototype && t.prototype.isPureReactComponent ? !uo(n, r) || !uo(o, i) : !0;
}
function Zh(e, t, n) {
  var r = !1, o = ln, i = t.contextType;
  return typeof i == "object" && i !== null ? i = tt(i) : (o = Ve(t) ? En : Le.current, r = t.contextTypes, i = (r = r != null) ? sr(e, o) : ln), t = new t(n, i), e.memoizedState = t.state !== null && t.state !== void 0 ? t.state : null, t.updater = ws, e.stateNode = t, t._reactInternals = e, r && (e = e.stateNode, e.__reactInternalMemoizedUnmaskedChildContext = o, e.__reactInternalMemoizedMaskedChildContext = i), t;
}
function Uc(e, t, n, r) {
  e = t.state, typeof t.componentWillReceiveProps == "function" && t.componentWillReceiveProps(n, r), typeof t.UNSAFE_componentWillReceiveProps == "function" && t.UNSAFE_componentWillReceiveProps(n, r), t.state !== e && ws.enqueueReplaceState(t, t.state, null);
}
function ru(e, t, n, r) {
  var o = e.stateNode;
  o.props = n, o.state = e.memoizedState, o.refs = {}, la(e);
  var i = t.contextType;
  typeof i == "object" && i !== null ? o.context = tt(i) : (i = Ve(t) ? En : Le.current, o.context = sr(e, i)), o.state = e.memoizedState, i = t.getDerivedStateFromProps, typeof i == "function" && (nu(e, t, i, n), o.state = e.memoizedState), typeof t.getDerivedStateFromProps == "function" || typeof o.getSnapshotBeforeUpdate == "function" || typeof o.UNSAFE_componentWillMount != "function" && typeof o.componentWillMount != "function" || (t = o.state, typeof o.componentWillMount == "function" && o.componentWillMount(), typeof o.UNSAFE_componentWillMount == "function" && o.UNSAFE_componentWillMount(), t !== o.state && ws.enqueueReplaceState(o, o.state, null), Xi(e, n, o, r), o.state = e.memoizedState), typeof o.componentDidMount == "function" && (e.flags |= 4194308);
}
function cr(e, t) {
  try {
    var n = "", r = t;
    do
      n += b0(r), r = r.return;
    while (r);
    var o = n;
  } catch (i) {
    o = `
Error generating stack: ` + i.message + `
` + i.stack;
  }
  return { value: e, source: t, stack: o, digest: null };
}
function dl(e, t, n) {
  return { value: e, source: null, stack: n ?? null, digest: t ?? null };
}
function ou(e, t) {
  try {
    console.error(t.value);
  } catch (n) {
    setTimeout(function() {
      throw n;
    });
  }
}
var Sv = typeof WeakMap == "function" ? WeakMap : Map;
function qh(e, t, n) {
  n = Tt(-1, n), n.tag = 3, n.payload = { element: null };
  var r = t.value;
  return n.callback = function() {
    Zi || (Zi = !0, pu = r), ou(e, t);
  }, n;
}
function Jh(e, t, n) {
  n = Tt(-1, n), n.tag = 3;
  var r = e.type.getDerivedStateFromError;
  if (typeof r == "function") {
    var o = t.value;
    n.payload = function() {
      return r(o);
    }, n.callback = function() {
      ou(e, t);
    };
  }
  var i = e.stateNode;
  return i !== null && typeof i.componentDidCatch == "function" && (n.callback = function() {
    ou(e, t), typeof r != "function" && (tn === null ? tn = /* @__PURE__ */ new Set([this]) : tn.add(this));
    var s = t.stack;
    this.componentDidCatch(t.value, { componentStack: s !== null ? s : "" });
  }), n;
}
function Wc(e, t, n) {
  var r = e.pingCache;
  if (r === null) {
    r = e.pingCache = new Sv();
    var o = /* @__PURE__ */ new Set();
    r.set(t, o);
  } else o = r.get(t), o === void 0 && (o = /* @__PURE__ */ new Set(), r.set(t, o));
  o.has(n) || (o.add(n), e = Dv.bind(null, e, t, n), t.then(e, e));
}
function Yc(e) {
  do {
    var t;
    if ((t = e.tag === 13) && (t = e.memoizedState, t = t !== null ? t.dehydrated !== null : !0), t) return e;
    e = e.return;
  } while (e !== null);
  return null;
}
function Xc(e, t, n, r, o) {
  return e.mode & 1 ? (e.flags |= 65536, e.lanes = o, e) : (e === t ? e.flags |= 65536 : (e.flags |= 128, n.flags |= 131072, n.flags &= -52805, n.tag === 1 && (n.alternate === null ? n.tag = 17 : (t = Tt(-1, 1), t.tag = 2, en(n, t, 1))), n.lanes |= 1), e);
}
var _v = jt.ReactCurrentOwner, Oe = !1;
function Ae(e, t, n, r) {
  t.child = e === null ? Ph(t, null, n, r) : ur(t, e.child, n, r);
}
function Qc(e, t, n, r, o) {
  n = n.render;
  var i = t.ref;
  return tr(t, o), r = da(e, t, n, r, i, o), n = ha(), e !== null && !Oe ? (t.updateQueue = e.updateQueue, t.flags &= -2053, e.lanes &= ~o, Dt(e, t, o)) : (fe && n && ea(t), t.flags |= 1, Ae(e, t, r, o), t.child);
}
function bc(e, t, n, r, o) {
  if (e === null) {
    var i = n.type;
    return typeof i == "function" && !Ea(i) && i.defaultProps === void 0 && n.compare === null && n.defaultProps === void 0 ? (t.tag = 15, t.type = i, ep(e, t, i, r, o)) : (e = Ni(n.type, null, r, t, t.mode, o), e.ref = t.ref, e.return = t, t.child = e);
  }
  if (i = e.child, !(e.lanes & o)) {
    var s = i.memoizedProps;
    if (n = n.compare, n = n !== null ? n : uo, n(s, r) && e.ref === t.ref) return Dt(e, t, o);
  }
  return t.flags |= 1, e = rn(i, r), e.ref = t.ref, e.return = t, t.child = e;
}
function ep(e, t, n, r, o) {
  if (e !== null) {
    var i = e.memoizedProps;
    if (uo(i, r) && e.ref === t.ref) if (Oe = !1, t.pendingProps = r = i, (e.lanes & o) !== 0) e.flags & 131072 && (Oe = !0);
    else return t.lanes = e.lanes, Dt(e, t, o);
  }
  return iu(e, t, n, r, o);
}
function tp(e, t, n) {
  var r = t.pendingProps, o = r.children, i = e !== null ? e.memoizedState : null;
  if (r.mode === "hidden") if (!(t.mode & 1)) t.memoizedState = { baseLanes: 0, cachePool: null, transitions: null }, le(bn, Ue), Ue |= n;
  else {
    if (!(n & 1073741824)) return e = i !== null ? i.baseLanes | n : n, t.lanes = t.childLanes = 1073741824, t.memoizedState = { baseLanes: e, cachePool: null, transitions: null }, t.updateQueue = null, le(bn, Ue), Ue |= e, null;
    t.memoizedState = { baseLanes: 0, cachePool: null, transitions: null }, r = i !== null ? i.baseLanes : n, le(bn, Ue), Ue |= r;
  }
  else i !== null ? (r = i.baseLanes | n, t.memoizedState = null) : r = n, le(bn, Ue), Ue |= r;
  return Ae(e, t, o, n), t.child;
}
function np(e, t) {
  var n = t.ref;
  (e === null && n !== null || e !== null && e.ref !== n) && (t.flags |= 512, t.flags |= 2097152);
}
function iu(e, t, n, r, o) {
  var i = Ve(n) ? En : Le.current;
  return i = sr(t, i), tr(t, o), n = da(e, t, n, r, i, o), r = ha(), e !== null && !Oe ? (t.updateQueue = e.updateQueue, t.flags &= -2053, e.lanes &= ~o, Dt(e, t, o)) : (fe && r && ea(t), t.flags |= 1, Ae(e, t, n, o), t.child);
}
function Kc(e, t, n, r, o) {
  if (Ve(n)) {
    var i = !0;
    Vi(t);
  } else i = !1;
  if (tr(t, o), t.stateNode === null) _i(e, t), Zh(t, n, r), ru(t, n, r, o), r = !0;
  else if (e === null) {
    var s = t.stateNode, l = t.memoizedProps;
    s.props = l;
    var u = s.context, a = n.contextType;
    typeof a == "object" && a !== null ? a = tt(a) : (a = Ve(n) ? En : Le.current, a = sr(t, a));
    var d = n.getDerivedStateFromProps, c = typeof d == "function" || typeof s.getSnapshotBeforeUpdate == "function";
    c || typeof s.UNSAFE_componentWillReceiveProps != "function" && typeof s.componentWillReceiveProps != "function" || (l !== r || u !== a) && Uc(t, s, r, a), Wt = !1;
    var f = t.memoizedState;
    s.state = f, Xi(t, r, s, o), u = t.memoizedState, l !== r || f !== u || He.current || Wt ? (typeof d == "function" && (nu(t, n, d, r), u = t.memoizedState), (l = Wt || Bc(t, n, l, r, f, u, a)) ? (c || typeof s.UNSAFE_componentWillMount != "function" && typeof s.componentWillMount != "function" || (typeof s.componentWillMount == "function" && s.componentWillMount(), typeof s.UNSAFE_componentWillMount == "function" && s.UNSAFE_componentWillMount()), typeof s.componentDidMount == "function" && (t.flags |= 4194308)) : (typeof s.componentDidMount == "function" && (t.flags |= 4194308), t.memoizedProps = r, t.memoizedState = u), s.props = r, s.state = u, s.context = a, r = l) : (typeof s.componentDidMount == "function" && (t.flags |= 4194308), r = !1);
  } else {
    s = t.stateNode, Ih(e, t), l = t.memoizedProps, a = t.type === t.elementType ? l : ot(t.type, l), s.props = a, c = t.pendingProps, f = s.context, u = n.contextType, typeof u == "object" && u !== null ? u = tt(u) : (u = Ve(n) ? En : Le.current, u = sr(t, u));
    var m = n.getDerivedStateFromProps;
    (d = typeof m == "function" || typeof s.getSnapshotBeforeUpdate == "function") || typeof s.UNSAFE_componentWillReceiveProps != "function" && typeof s.componentWillReceiveProps != "function" || (l !== c || f !== u) && Uc(t, s, r, u), Wt = !1, f = t.memoizedState, s.state = f, Xi(t, r, s, o);
    var y = t.memoizedState;
    l !== c || f !== y || He.current || Wt ? (typeof m == "function" && (nu(t, n, m, r), y = t.memoizedState), (a = Wt || Bc(t, n, a, r, f, y, u) || !1) ? (d || typeof s.UNSAFE_componentWillUpdate != "function" && typeof s.componentWillUpdate != "function" || (typeof s.componentWillUpdate == "function" && s.componentWillUpdate(r, y, u), typeof s.UNSAFE_componentWillUpdate == "function" && s.UNSAFE_componentWillUpdate(r, y, u)), typeof s.componentDidUpdate == "function" && (t.flags |= 4), typeof s.getSnapshotBeforeUpdate == "function" && (t.flags |= 1024)) : (typeof s.componentDidUpdate != "function" || l === e.memoizedProps && f === e.memoizedState || (t.flags |= 4), typeof s.getSnapshotBeforeUpdate != "function" || l === e.memoizedProps && f === e.memoizedState || (t.flags |= 1024), t.memoizedProps = r, t.memoizedState = y), s.props = r, s.state = y, s.context = u, r = a) : (typeof s.componentDidUpdate != "function" || l === e.memoizedProps && f === e.memoizedState || (t.flags |= 4), typeof s.getSnapshotBeforeUpdate != "function" || l === e.memoizedProps && f === e.memoizedState || (t.flags |= 1024), r = !1);
  }
  return su(e, t, n, r, i, o);
}
function su(e, t, n, r, o, i) {
  np(e, t);
  var s = (t.flags & 128) !== 0;
  if (!r && !s) return o && $c(t, n, !1), Dt(e, t, i);
  r = t.stateNode, _v.current = t;
  var l = s && typeof n.getDerivedStateFromError != "function" ? null : r.render();
  return t.flags |= 1, e !== null && s ? (t.child = ur(t, e.child, null, i), t.child = ur(t, null, l, i)) : Ae(e, t, l, i), t.memoizedState = r.state, o && $c(t, n, !0), t.child;
}
function rp(e) {
  var t = e.stateNode;
  t.pendingContext ? Ac(e, t.pendingContext, t.pendingContext !== t.context) : t.context && Ac(e, t.context, !1), ua(e, t.containerInfo);
}
function Gc(e, t, n, r, o) {
  return lr(), na(o), t.flags |= 256, Ae(e, t, n, r), t.child;
}
var lu = { dehydrated: null, treeContext: null, retryLane: 0 };
function uu(e) {
  return { baseLanes: e, cachePool: null, transitions: null };
}
function op(e, t, n) {
  var r = t.pendingProps, o = pe.current, i = !1, s = (t.flags & 128) !== 0, l;
  if ((l = s) || (l = e !== null && e.memoizedState === null ? !1 : (o & 2) !== 0), l ? (i = !0, t.flags &= -129) : (e === null || e.memoizedState !== null) && (o |= 1), le(pe, o & 1), e === null)
    return eu(t), e = t.memoizedState, e !== null && (e = e.dehydrated, e !== null) ? (t.mode & 1 ? e.data === "$!" ? t.lanes = 8 : t.lanes = 1073741824 : t.lanes = 1, null) : (s = r.children, e = r.fallback, i ? (r = t.mode, i = t.child, s = { mode: "hidden", children: s }, !(r & 1) && i !== null ? (i.childLanes = 0, i.pendingProps = s) : i = _s(s, r, 0, null), e = xn(e, r, n, null), i.return = t, e.return = t, i.sibling = e, t.child = i, t.child.memoizedState = uu(n), t.memoizedState = lu, e) : ma(t, s));
  if (o = e.memoizedState, o !== null && (l = o.dehydrated, l !== null)) return Ev(e, t, s, r, l, o, n);
  if (i) {
    i = r.fallback, s = t.mode, o = e.child, l = o.sibling;
    var u = { mode: "hidden", children: r.children };
    return !(s & 1) && t.child !== o ? (r = t.child, r.childLanes = 0, r.pendingProps = u, t.deletions = null) : (r = rn(o, u), r.subtreeFlags = o.subtreeFlags & 14680064), l !== null ? i = rn(l, i) : (i = xn(i, s, n, null), i.flags |= 2), i.return = t, r.return = t, r.sibling = i, t.child = r, r = i, i = t.child, s = e.child.memoizedState, s = s === null ? uu(n) : { baseLanes: s.baseLanes | n, cachePool: null, transitions: s.transitions }, i.memoizedState = s, i.childLanes = e.childLanes & ~n, t.memoizedState = lu, r;
  }
  return i = e.child, e = i.sibling, r = rn(i, { mode: "visible", children: r.children }), !(t.mode & 1) && (r.lanes = n), r.return = t, r.sibling = null, e !== null && (n = t.deletions, n === null ? (t.deletions = [e], t.flags |= 16) : n.push(e)), t.child = r, t.memoizedState = null, r;
}
function ma(e, t) {
  return t = _s({ mode: "visible", children: t }, e.mode, 0, null), t.return = e, e.child = t;
}
function ti(e, t, n, r) {
  return r !== null && na(r), ur(t, e.child, null, n), e = ma(t, t.pendingProps.children), e.flags |= 2, t.memoizedState = null, e;
}
function Ev(e, t, n, r, o, i, s) {
  if (n)
    return t.flags & 256 ? (t.flags &= -257, r = dl(Error(V(422))), ti(e, t, s, r)) : t.memoizedState !== null ? (t.child = e.child, t.flags |= 128, null) : (i = r.fallback, o = t.mode, r = _s({ mode: "visible", children: r.children }, o, 0, null), i = xn(i, o, s, null), i.flags |= 2, r.return = t, i.return = t, r.sibling = i, t.child = r, t.mode & 1 && ur(t, e.child, null, s), t.child.memoizedState = uu(s), t.memoizedState = lu, i);
  if (!(t.mode & 1)) return ti(e, t, s, null);
  if (o.data === "$!") {
    if (r = o.nextSibling && o.nextSibling.dataset, r) var l = r.dgst;
    return r = l, i = Error(V(419)), r = dl(i, r, void 0), ti(e, t, s, r);
  }
  if (l = (s & e.childLanes) !== 0, Oe || l) {
    if (r = ke, r !== null) {
      switch (s & -s) {
        case 4:
          o = 2;
          break;
        case 16:
          o = 8;
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
          o = 32;
          break;
        case 536870912:
          o = 268435456;
          break;
        default:
          o = 0;
      }
      o = o & (r.suspendedLanes | s) ? 0 : o, o !== 0 && o !== i.retryLane && (i.retryLane = o, $t(e, o), dt(r, e, o, -1));
    }
    return _a(), r = dl(Error(V(421))), ti(e, t, s, r);
  }
  return o.data === "$?" ? (t.flags |= 128, t.child = e.child, t = Rv.bind(null, e), o._reactRetry = t, null) : (e = i.treeContext, Ye = Jt(o.nextSibling), Xe = t, fe = !0, lt = null, e !== null && (Ze[qe++] = Pt, Ze[qe++] = zt, Ze[qe++] = kn, Pt = e.id, zt = e.overflow, kn = t), t = ma(t, r.children), t.flags |= 4096, t);
}
function Zc(e, t, n) {
  e.lanes |= t;
  var r = e.alternate;
  r !== null && (r.lanes |= t), tu(e.return, t, n);
}
function hl(e, t, n, r, o) {
  var i = e.memoizedState;
  i === null ? e.memoizedState = { isBackwards: t, rendering: null, renderingStartTime: 0, last: r, tail: n, tailMode: o } : (i.isBackwards = t, i.rendering = null, i.renderingStartTime = 0, i.last = r, i.tail = n, i.tailMode = o);
}
function ip(e, t, n) {
  var r = t.pendingProps, o = r.revealOrder, i = r.tail;
  if (Ae(e, t, r.children, n), r = pe.current, r & 2) r = r & 1 | 2, t.flags |= 128;
  else {
    if (e !== null && e.flags & 128) e: for (e = t.child; e !== null; ) {
      if (e.tag === 13) e.memoizedState !== null && Zc(e, n, t);
      else if (e.tag === 19) Zc(e, n, t);
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
  if (le(pe, r), !(t.mode & 1)) t.memoizedState = null;
  else switch (o) {
    case "forwards":
      for (n = t.child, o = null; n !== null; ) e = n.alternate, e !== null && Qi(e) === null && (o = n), n = n.sibling;
      n = o, n === null ? (o = t.child, t.child = null) : (o = n.sibling, n.sibling = null), hl(t, !1, o, n, i);
      break;
    case "backwards":
      for (n = null, o = t.child, t.child = null; o !== null; ) {
        if (e = o.alternate, e !== null && Qi(e) === null) {
          t.child = o;
          break;
        }
        e = o.sibling, o.sibling = n, n = o, o = e;
      }
      hl(t, !0, n, null, i);
      break;
    case "together":
      hl(t, !1, null, null, void 0);
      break;
    default:
      t.memoizedState = null;
  }
  return t.child;
}
function _i(e, t) {
  !(t.mode & 1) && e !== null && (e.alternate = null, t.alternate = null, t.flags |= 2);
}
function Dt(e, t, n) {
  if (e !== null && (t.dependencies = e.dependencies), Cn |= t.lanes, !(n & t.childLanes)) return null;
  if (e !== null && t.child !== e.child) throw Error(V(153));
  if (t.child !== null) {
    for (e = t.child, n = rn(e, e.pendingProps), t.child = n, n.return = t; e.sibling !== null; ) e = e.sibling, n = n.sibling = rn(e, e.pendingProps), n.return = t;
    n.sibling = null;
  }
  return t.child;
}
function kv(e, t, n) {
  switch (t.tag) {
    case 3:
      rp(t), lr();
      break;
    case 5:
      Th(t);
      break;
    case 1:
      Ve(t.type) && Vi(t);
      break;
    case 4:
      ua(t, t.stateNode.containerInfo);
      break;
    case 10:
      var r = t.type._context, o = t.memoizedProps.value;
      le(Wi, r._currentValue), r._currentValue = o;
      break;
    case 13:
      if (r = t.memoizedState, r !== null)
        return r.dehydrated !== null ? (le(pe, pe.current & 1), t.flags |= 128, null) : n & t.child.childLanes ? op(e, t, n) : (le(pe, pe.current & 1), e = Dt(e, t, n), e !== null ? e.sibling : null);
      le(pe, pe.current & 1);
      break;
    case 19:
      if (r = (n & t.childLanes) !== 0, e.flags & 128) {
        if (r) return ip(e, t, n);
        t.flags |= 128;
      }
      if (o = t.memoizedState, o !== null && (o.rendering = null, o.tail = null, o.lastEffect = null), le(pe, pe.current), r) break;
      return null;
    case 22:
    case 23:
      return t.lanes = 0, tp(e, t, n);
  }
  return Dt(e, t, n);
}
var sp, au, lp, up;
sp = function(e, t) {
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
};
au = function() {
};
lp = function(e, t, n, r) {
  var o = e.memoizedProps;
  if (o !== r) {
    e = t.stateNode, yn(xt.current);
    var i = null;
    switch (n) {
      case "input":
        o = Tl(e, o), r = Tl(e, r), i = [];
        break;
      case "select":
        o = me({}, o, { value: void 0 }), r = me({}, r, { value: void 0 }), i = [];
        break;
      case "textarea":
        o = $l(e, o), r = $l(e, r), i = [];
        break;
      default:
        typeof o.onClick != "function" && typeof r.onClick == "function" && (e.onclick = Fi);
    }
    Rl(n, r);
    var s;
    n = null;
    for (a in o) if (!r.hasOwnProperty(a) && o.hasOwnProperty(a) && o[a] != null) if (a === "style") {
      var l = o[a];
      for (s in l) l.hasOwnProperty(s) && (n || (n = {}), n[s] = "");
    } else a !== "dangerouslySetInnerHTML" && a !== "children" && a !== "suppressContentEditableWarning" && a !== "suppressHydrationWarning" && a !== "autoFocus" && (to.hasOwnProperty(a) ? i || (i = []) : (i = i || []).push(a, null));
    for (a in r) {
      var u = r[a];
      if (l = o != null ? o[a] : void 0, r.hasOwnProperty(a) && u !== l && (u != null || l != null)) if (a === "style") if (l) {
        for (s in l) !l.hasOwnProperty(s) || u && u.hasOwnProperty(s) || (n || (n = {}), n[s] = "");
        for (s in u) u.hasOwnProperty(s) && l[s] !== u[s] && (n || (n = {}), n[s] = u[s]);
      } else n || (i || (i = []), i.push(
        a,
        n
      )), n = u;
      else a === "dangerouslySetInnerHTML" ? (u = u ? u.__html : void 0, l = l ? l.__html : void 0, u != null && l !== u && (i = i || []).push(a, u)) : a === "children" ? typeof u != "string" && typeof u != "number" || (i = i || []).push(a, "" + u) : a !== "suppressContentEditableWarning" && a !== "suppressHydrationWarning" && (to.hasOwnProperty(a) ? (u != null && a === "onScroll" && ae("scroll", e), i || l === u || (i = [])) : (i = i || []).push(a, u));
    }
    n && (i = i || []).push("style", n);
    var a = i;
    (t.updateQueue = a) && (t.flags |= 4);
  }
};
up = function(e, t, n, r) {
  n !== r && (t.flags |= 4);
};
function $r(e, t) {
  if (!fe) switch (e.tailMode) {
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
function Ie(e) {
  var t = e.alternate !== null && e.alternate.child === e.child, n = 0, r = 0;
  if (t) for (var o = e.child; o !== null; ) n |= o.lanes | o.childLanes, r |= o.subtreeFlags & 14680064, r |= o.flags & 14680064, o.return = e, o = o.sibling;
  else for (o = e.child; o !== null; ) n |= o.lanes | o.childLanes, r |= o.subtreeFlags, r |= o.flags, o.return = e, o = o.sibling;
  return e.subtreeFlags |= r, e.childLanes = n, t;
}
function Nv(e, t, n) {
  var r = t.pendingProps;
  switch (ta(t), t.tag) {
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
      return Ie(t), null;
    case 1:
      return Ve(t.type) && Hi(), Ie(t), null;
    case 3:
      return r = t.stateNode, ar(), ce(He), ce(Le), ca(), r.pendingContext && (r.context = r.pendingContext, r.pendingContext = null), (e === null || e.child === null) && (Jo(t) ? t.flags |= 4 : e === null || e.memoizedState.isDehydrated && !(t.flags & 256) || (t.flags |= 1024, lt !== null && (yu(lt), lt = null))), au(e, t), Ie(t), null;
    case 5:
      aa(t);
      var o = yn(po.current);
      if (n = t.type, e !== null && t.stateNode != null) lp(e, t, n, r, o), e.ref !== t.ref && (t.flags |= 512, t.flags |= 2097152);
      else {
        if (!r) {
          if (t.stateNode === null) throw Error(V(166));
          return Ie(t), null;
        }
        if (e = yn(xt.current), Jo(t)) {
          r = t.stateNode, n = t.type;
          var i = t.memoizedProps;
          switch (r[vt] = t, r[fo] = i, e = (t.mode & 1) !== 0, n) {
            case "dialog":
              ae("cancel", r), ae("close", r);
              break;
            case "iframe":
            case "object":
            case "embed":
              ae("load", r);
              break;
            case "video":
            case "audio":
              for (o = 0; o < Vr.length; o++) ae(Vr[o], r);
              break;
            case "source":
              ae("error", r);
              break;
            case "img":
            case "image":
            case "link":
              ae(
                "error",
                r
              ), ae("load", r);
              break;
            case "details":
              ae("toggle", r);
              break;
            case "input":
              sc(r, i), ae("invalid", r);
              break;
            case "select":
              r._wrapperState = { wasMultiple: !!i.multiple }, ae("invalid", r);
              break;
            case "textarea":
              uc(r, i), ae("invalid", r);
          }
          Rl(n, i), o = null;
          for (var s in i) if (i.hasOwnProperty(s)) {
            var l = i[s];
            s === "children" ? typeof l == "string" ? r.textContent !== l && (i.suppressHydrationWarning !== !0 && qo(r.textContent, l, e), o = ["children", l]) : typeof l == "number" && r.textContent !== "" + l && (i.suppressHydrationWarning !== !0 && qo(
              r.textContent,
              l,
              e
            ), o = ["children", "" + l]) : to.hasOwnProperty(s) && l != null && s === "onScroll" && ae("scroll", r);
          }
          switch (n) {
            case "input":
              Wo(r), lc(r, i, !0);
              break;
            case "textarea":
              Wo(r), ac(r);
              break;
            case "select":
            case "option":
              break;
            default:
              typeof i.onClick == "function" && (r.onclick = Fi);
          }
          r = o, t.updateQueue = r, r !== null && (t.flags |= 4);
        } else {
          s = o.nodeType === 9 ? o : o.ownerDocument, e === "http://www.w3.org/1999/xhtml" && (e = Rd(n)), e === "http://www.w3.org/1999/xhtml" ? n === "script" ? (e = s.createElement("div"), e.innerHTML = "<script><\/script>", e = e.removeChild(e.firstChild)) : typeof r.is == "string" ? e = s.createElement(n, { is: r.is }) : (e = s.createElement(n), n === "select" && (s = e, r.multiple ? s.multiple = !0 : r.size && (s.size = r.size))) : e = s.createElementNS(e, n), e[vt] = t, e[fo] = r, sp(e, t, !1, !1), t.stateNode = e;
          e: {
            switch (s = jl(n, r), n) {
              case "dialog":
                ae("cancel", e), ae("close", e), o = r;
                break;
              case "iframe":
              case "object":
              case "embed":
                ae("load", e), o = r;
                break;
              case "video":
              case "audio":
                for (o = 0; o < Vr.length; o++) ae(Vr[o], e);
                o = r;
                break;
              case "source":
                ae("error", e), o = r;
                break;
              case "img":
              case "image":
              case "link":
                ae(
                  "error",
                  e
                ), ae("load", e), o = r;
                break;
              case "details":
                ae("toggle", e), o = r;
                break;
              case "input":
                sc(e, r), o = Tl(e, r), ae("invalid", e);
                break;
              case "option":
                o = r;
                break;
              case "select":
                e._wrapperState = { wasMultiple: !!r.multiple }, o = me({}, r, { value: void 0 }), ae("invalid", e);
                break;
              case "textarea":
                uc(e, r), o = $l(e, r), ae("invalid", e);
                break;
              default:
                o = r;
            }
            Rl(n, o), l = o;
            for (i in l) if (l.hasOwnProperty(i)) {
              var u = l[i];
              i === "style" ? Fd(e, u) : i === "dangerouslySetInnerHTML" ? (u = u ? u.__html : void 0, u != null && jd(e, u)) : i === "children" ? typeof u == "string" ? (n !== "textarea" || u !== "") && no(e, u) : typeof u == "number" && no(e, "" + u) : i !== "suppressContentEditableWarning" && i !== "suppressHydrationWarning" && i !== "autoFocus" && (to.hasOwnProperty(i) ? u != null && i === "onScroll" && ae("scroll", e) : u != null && Hu(e, i, u, s));
            }
            switch (n) {
              case "input":
                Wo(e), lc(e, r, !1);
                break;
              case "textarea":
                Wo(e), ac(e);
                break;
              case "option":
                r.value != null && e.setAttribute("value", "" + sn(r.value));
                break;
              case "select":
                e.multiple = !!r.multiple, i = r.value, i != null ? Zn(e, !!r.multiple, i, !1) : r.defaultValue != null && Zn(
                  e,
                  !!r.multiple,
                  r.defaultValue,
                  !0
                );
                break;
              default:
                typeof o.onClick == "function" && (e.onclick = Fi);
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
      return Ie(t), null;
    case 6:
      if (e && t.stateNode != null) up(e, t, e.memoizedProps, r);
      else {
        if (typeof r != "string" && t.stateNode === null) throw Error(V(166));
        if (n = yn(po.current), yn(xt.current), Jo(t)) {
          if (r = t.stateNode, n = t.memoizedProps, r[vt] = t, (i = r.nodeValue !== n) && (e = Xe, e !== null)) switch (e.tag) {
            case 3:
              qo(r.nodeValue, n, (e.mode & 1) !== 0);
              break;
            case 5:
              e.memoizedProps.suppressHydrationWarning !== !0 && qo(r.nodeValue, n, (e.mode & 1) !== 0);
          }
          i && (t.flags |= 4);
        } else r = (n.nodeType === 9 ? n : n.ownerDocument).createTextNode(r), r[vt] = t, t.stateNode = r;
      }
      return Ie(t), null;
    case 13:
      if (ce(pe), r = t.memoizedState, e === null || e.memoizedState !== null && e.memoizedState.dehydrated !== null) {
        if (fe && Ye !== null && t.mode & 1 && !(t.flags & 128)) Ch(), lr(), t.flags |= 98560, i = !1;
        else if (i = Jo(t), r !== null && r.dehydrated !== null) {
          if (e === null) {
            if (!i) throw Error(V(318));
            if (i = t.memoizedState, i = i !== null ? i.dehydrated : null, !i) throw Error(V(317));
            i[vt] = t;
          } else lr(), !(t.flags & 128) && (t.memoizedState = null), t.flags |= 4;
          Ie(t), i = !1;
        } else lt !== null && (yu(lt), lt = null), i = !0;
        if (!i) return t.flags & 65536 ? t : null;
      }
      return t.flags & 128 ? (t.lanes = n, t) : (r = r !== null, r !== (e !== null && e.memoizedState !== null) && r && (t.child.flags |= 8192, t.mode & 1 && (e === null || pe.current & 1 ? _e === 0 && (_e = 3) : _a())), t.updateQueue !== null && (t.flags |= 4), Ie(t), null);
    case 4:
      return ar(), au(e, t), e === null && ao(t.stateNode.containerInfo), Ie(t), null;
    case 10:
      return ia(t.type._context), Ie(t), null;
    case 17:
      return Ve(t.type) && Hi(), Ie(t), null;
    case 19:
      if (ce(pe), i = t.memoizedState, i === null) return Ie(t), null;
      if (r = (t.flags & 128) !== 0, s = i.rendering, s === null) if (r) $r(i, !1);
      else {
        if (_e !== 0 || e !== null && e.flags & 128) for (e = t.child; e !== null; ) {
          if (s = Qi(e), s !== null) {
            for (t.flags |= 128, $r(i, !1), r = s.updateQueue, r !== null && (t.updateQueue = r, t.flags |= 4), t.subtreeFlags = 0, r = n, n = t.child; n !== null; ) i = n, e = r, i.flags &= 14680066, s = i.alternate, s === null ? (i.childLanes = 0, i.lanes = e, i.child = null, i.subtreeFlags = 0, i.memoizedProps = null, i.memoizedState = null, i.updateQueue = null, i.dependencies = null, i.stateNode = null) : (i.childLanes = s.childLanes, i.lanes = s.lanes, i.child = s.child, i.subtreeFlags = 0, i.deletions = null, i.memoizedProps = s.memoizedProps, i.memoizedState = s.memoizedState, i.updateQueue = s.updateQueue, i.type = s.type, e = s.dependencies, i.dependencies = e === null ? null : { lanes: e.lanes, firstContext: e.firstContext }), n = n.sibling;
            return le(pe, pe.current & 1 | 2), t.child;
          }
          e = e.sibling;
        }
        i.tail !== null && ve() > fr && (t.flags |= 128, r = !0, $r(i, !1), t.lanes = 4194304);
      }
      else {
        if (!r) if (e = Qi(s), e !== null) {
          if (t.flags |= 128, r = !0, n = e.updateQueue, n !== null && (t.updateQueue = n, t.flags |= 4), $r(i, !0), i.tail === null && i.tailMode === "hidden" && !s.alternate && !fe) return Ie(t), null;
        } else 2 * ve() - i.renderingStartTime > fr && n !== 1073741824 && (t.flags |= 128, r = !0, $r(i, !1), t.lanes = 4194304);
        i.isBackwards ? (s.sibling = t.child, t.child = s) : (n = i.last, n !== null ? n.sibling = s : t.child = s, i.last = s);
      }
      return i.tail !== null ? (t = i.tail, i.rendering = t, i.tail = t.sibling, i.renderingStartTime = ve(), t.sibling = null, n = pe.current, le(pe, r ? n & 1 | 2 : n & 1), t) : (Ie(t), null);
    case 22:
    case 23:
      return Sa(), r = t.memoizedState !== null, e !== null && e.memoizedState !== null !== r && (t.flags |= 8192), r && t.mode & 1 ? Ue & 1073741824 && (Ie(t), t.subtreeFlags & 6 && (t.flags |= 8192)) : Ie(t), null;
    case 24:
      return null;
    case 25:
      return null;
  }
  throw Error(V(156, t.tag));
}
function Cv(e, t) {
  switch (ta(t), t.tag) {
    case 1:
      return Ve(t.type) && Hi(), e = t.flags, e & 65536 ? (t.flags = e & -65537 | 128, t) : null;
    case 3:
      return ar(), ce(He), ce(Le), ca(), e = t.flags, e & 65536 && !(e & 128) ? (t.flags = e & -65537 | 128, t) : null;
    case 5:
      return aa(t), null;
    case 13:
      if (ce(pe), e = t.memoizedState, e !== null && e.dehydrated !== null) {
        if (t.alternate === null) throw Error(V(340));
        lr();
      }
      return e = t.flags, e & 65536 ? (t.flags = e & -65537 | 128, t) : null;
    case 19:
      return ce(pe), null;
    case 4:
      return ar(), null;
    case 10:
      return ia(t.type._context), null;
    case 22:
    case 23:
      return Sa(), null;
    case 24:
      return null;
    default:
      return null;
  }
}
var ni = !1, Te = !1, Mv = typeof WeakSet == "function" ? WeakSet : Set, Y = null;
function Qn(e, t) {
  var n = e.ref;
  if (n !== null) if (typeof n == "function") try {
    n(null);
  } catch (r) {
    ye(e, t, r);
  }
  else n.current = null;
}
function cu(e, t, n) {
  try {
    n();
  } catch (r) {
    ye(e, t, r);
  }
}
var qc = !1;
function Pv(e, t) {
  if (Ql = Ri, e = hh(), Ju(e)) {
    if ("selectionStart" in e) var n = { start: e.selectionStart, end: e.selectionEnd };
    else e: {
      n = (n = e.ownerDocument) && n.defaultView || window;
      var r = n.getSelection && n.getSelection();
      if (r && r.rangeCount !== 0) {
        n = r.anchorNode;
        var o = r.anchorOffset, i = r.focusNode;
        r = r.focusOffset;
        try {
          n.nodeType, i.nodeType;
        } catch {
          n = null;
          break e;
        }
        var s = 0, l = -1, u = -1, a = 0, d = 0, c = e, f = null;
        t: for (; ; ) {
          for (var m; c !== n || o !== 0 && c.nodeType !== 3 || (l = s + o), c !== i || r !== 0 && c.nodeType !== 3 || (u = s + r), c.nodeType === 3 && (s += c.nodeValue.length), (m = c.firstChild) !== null; )
            f = c, c = m;
          for (; ; ) {
            if (c === e) break t;
            if (f === n && ++a === o && (l = s), f === i && ++d === r && (u = s), (m = c.nextSibling) !== null) break;
            c = f, f = c.parentNode;
          }
          c = m;
        }
        n = l === -1 || u === -1 ? null : { start: l, end: u };
      } else n = null;
    }
    n = n || { start: 0, end: 0 };
  } else n = null;
  for (bl = { focusedElem: e, selectionRange: n }, Ri = !1, Y = t; Y !== null; ) if (t = Y, e = t.child, (t.subtreeFlags & 1028) !== 0 && e !== null) e.return = t, Y = e;
  else for (; Y !== null; ) {
    t = Y;
    try {
      var y = t.alternate;
      if (t.flags & 1024) switch (t.tag) {
        case 0:
        case 11:
        case 15:
          break;
        case 1:
          if (y !== null) {
            var w = y.memoizedProps, x = y.memoizedState, p = t.stateNode, g = p.getSnapshotBeforeUpdate(t.elementType === t.type ? w : ot(t.type, w), x);
            p.__reactInternalSnapshotBeforeUpdate = g;
          }
          break;
        case 3:
          var h = t.stateNode.containerInfo;
          h.nodeType === 1 ? h.textContent = "" : h.nodeType === 9 && h.documentElement && h.removeChild(h.documentElement);
          break;
        case 5:
        case 6:
        case 4:
        case 17:
          break;
        default:
          throw Error(V(163));
      }
    } catch (v) {
      ye(t, t.return, v);
    }
    if (e = t.sibling, e !== null) {
      e.return = t.return, Y = e;
      break;
    }
    Y = t.return;
  }
  return y = qc, qc = !1, y;
}
function Gr(e, t, n) {
  var r = t.updateQueue;
  if (r = r !== null ? r.lastEffect : null, r !== null) {
    var o = r = r.next;
    do {
      if ((o.tag & e) === e) {
        var i = o.destroy;
        o.destroy = void 0, i !== void 0 && cu(t, n, i);
      }
      o = o.next;
    } while (o !== r);
  }
}
function xs(e, t) {
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
function fu(e) {
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
function ap(e) {
  var t = e.alternate;
  t !== null && (e.alternate = null, ap(t)), e.child = null, e.deletions = null, e.sibling = null, e.tag === 5 && (t = e.stateNode, t !== null && (delete t[vt], delete t[fo], delete t[Zl], delete t[cv], delete t[fv])), e.stateNode = null, e.return = null, e.dependencies = null, e.memoizedProps = null, e.memoizedState = null, e.pendingProps = null, e.stateNode = null, e.updateQueue = null;
}
function cp(e) {
  return e.tag === 5 || e.tag === 3 || e.tag === 4;
}
function Jc(e) {
  e: for (; ; ) {
    for (; e.sibling === null; ) {
      if (e.return === null || cp(e.return)) return null;
      e = e.return;
    }
    for (e.sibling.return = e.return, e = e.sibling; e.tag !== 5 && e.tag !== 6 && e.tag !== 18; ) {
      if (e.flags & 2 || e.child === null || e.tag === 4) continue e;
      e.child.return = e, e = e.child;
    }
    if (!(e.flags & 2)) return e.stateNode;
  }
}
function du(e, t, n) {
  var r = e.tag;
  if (r === 5 || r === 6) e = e.stateNode, t ? n.nodeType === 8 ? n.parentNode.insertBefore(e, t) : n.insertBefore(e, t) : (n.nodeType === 8 ? (t = n.parentNode, t.insertBefore(e, n)) : (t = n, t.appendChild(e)), n = n._reactRootContainer, n != null || t.onclick !== null || (t.onclick = Fi));
  else if (r !== 4 && (e = e.child, e !== null)) for (du(e, t, n), e = e.sibling; e !== null; ) du(e, t, n), e = e.sibling;
}
function hu(e, t, n) {
  var r = e.tag;
  if (r === 5 || r === 6) e = e.stateNode, t ? n.insertBefore(e, t) : n.appendChild(e);
  else if (r !== 4 && (e = e.child, e !== null)) for (hu(e, t, n), e = e.sibling; e !== null; ) hu(e, t, n), e = e.sibling;
}
var Ne = null, it = !1;
function Ht(e, t, n) {
  for (n = n.child; n !== null; ) fp(e, t, n), n = n.sibling;
}
function fp(e, t, n) {
  if (wt && typeof wt.onCommitFiberUnmount == "function") try {
    wt.onCommitFiberUnmount(ds, n);
  } catch {
  }
  switch (n.tag) {
    case 5:
      Te || Qn(n, t);
    case 6:
      var r = Ne, o = it;
      Ne = null, Ht(e, t, n), Ne = r, it = o, Ne !== null && (it ? (e = Ne, n = n.stateNode, e.nodeType === 8 ? e.parentNode.removeChild(n) : e.removeChild(n)) : Ne.removeChild(n.stateNode));
      break;
    case 18:
      Ne !== null && (it ? (e = Ne, n = n.stateNode, e.nodeType === 8 ? sl(e.parentNode, n) : e.nodeType === 1 && sl(e, n), so(e)) : sl(Ne, n.stateNode));
      break;
    case 4:
      r = Ne, o = it, Ne = n.stateNode.containerInfo, it = !0, Ht(e, t, n), Ne = r, it = o;
      break;
    case 0:
    case 11:
    case 14:
    case 15:
      if (!Te && (r = n.updateQueue, r !== null && (r = r.lastEffect, r !== null))) {
        o = r = r.next;
        do {
          var i = o, s = i.destroy;
          i = i.tag, s !== void 0 && (i & 2 || i & 4) && cu(n, t, s), o = o.next;
        } while (o !== r);
      }
      Ht(e, t, n);
      break;
    case 1:
      if (!Te && (Qn(n, t), r = n.stateNode, typeof r.componentWillUnmount == "function")) try {
        r.props = n.memoizedProps, r.state = n.memoizedState, r.componentWillUnmount();
      } catch (l) {
        ye(n, t, l);
      }
      Ht(e, t, n);
      break;
    case 21:
      Ht(e, t, n);
      break;
    case 22:
      n.mode & 1 ? (Te = (r = Te) || n.memoizedState !== null, Ht(e, t, n), Te = r) : Ht(e, t, n);
      break;
    default:
      Ht(e, t, n);
  }
}
function ef(e) {
  var t = e.updateQueue;
  if (t !== null) {
    e.updateQueue = null;
    var n = e.stateNode;
    n === null && (n = e.stateNode = new Mv()), t.forEach(function(r) {
      var o = jv.bind(null, e, r);
      n.has(r) || (n.add(r), r.then(o, o));
    });
  }
}
function rt(e, t) {
  var n = t.deletions;
  if (n !== null) for (var r = 0; r < n.length; r++) {
    var o = n[r];
    try {
      var i = e, s = t, l = s;
      e: for (; l !== null; ) {
        switch (l.tag) {
          case 5:
            Ne = l.stateNode, it = !1;
            break e;
          case 3:
            Ne = l.stateNode.containerInfo, it = !0;
            break e;
          case 4:
            Ne = l.stateNode.containerInfo, it = !0;
            break e;
        }
        l = l.return;
      }
      if (Ne === null) throw Error(V(160));
      fp(i, s, o), Ne = null, it = !1;
      var u = o.alternate;
      u !== null && (u.return = null), o.return = null;
    } catch (a) {
      ye(o, t, a);
    }
  }
  if (t.subtreeFlags & 12854) for (t = t.child; t !== null; ) dp(t, e), t = t.sibling;
}
function dp(e, t) {
  var n = e.alternate, r = e.flags;
  switch (e.tag) {
    case 0:
    case 11:
    case 14:
    case 15:
      if (rt(t, e), gt(e), r & 4) {
        try {
          Gr(3, e, e.return), xs(3, e);
        } catch (w) {
          ye(e, e.return, w);
        }
        try {
          Gr(5, e, e.return);
        } catch (w) {
          ye(e, e.return, w);
        }
      }
      break;
    case 1:
      rt(t, e), gt(e), r & 512 && n !== null && Qn(n, n.return);
      break;
    case 5:
      if (rt(t, e), gt(e), r & 512 && n !== null && Qn(n, n.return), e.flags & 32) {
        var o = e.stateNode;
        try {
          no(o, "");
        } catch (w) {
          ye(e, e.return, w);
        }
      }
      if (r & 4 && (o = e.stateNode, o != null)) {
        var i = e.memoizedProps, s = n !== null ? n.memoizedProps : i, l = e.type, u = e.updateQueue;
        if (e.updateQueue = null, u !== null) try {
          l === "input" && i.type === "radio" && i.name != null && $d(o, i), jl(l, s);
          var a = jl(l, i);
          for (s = 0; s < u.length; s += 2) {
            var d = u[s], c = u[s + 1];
            d === "style" ? Fd(o, c) : d === "dangerouslySetInnerHTML" ? jd(o, c) : d === "children" ? no(o, c) : Hu(o, d, c, a);
          }
          switch (l) {
            case "input":
              Ll(o, i);
              break;
            case "textarea":
              Dd(o, i);
              break;
            case "select":
              var f = o._wrapperState.wasMultiple;
              o._wrapperState.wasMultiple = !!i.multiple;
              var m = i.value;
              m != null ? Zn(o, !!i.multiple, m, !1) : f !== !!i.multiple && (i.defaultValue != null ? Zn(
                o,
                !!i.multiple,
                i.defaultValue,
                !0
              ) : Zn(o, !!i.multiple, i.multiple ? [] : "", !1));
          }
          o[fo] = i;
        } catch (w) {
          ye(e, e.return, w);
        }
      }
      break;
    case 6:
      if (rt(t, e), gt(e), r & 4) {
        if (e.stateNode === null) throw Error(V(162));
        o = e.stateNode, i = e.memoizedProps;
        try {
          o.nodeValue = i;
        } catch (w) {
          ye(e, e.return, w);
        }
      }
      break;
    case 3:
      if (rt(t, e), gt(e), r & 4 && n !== null && n.memoizedState.isDehydrated) try {
        so(t.containerInfo);
      } catch (w) {
        ye(e, e.return, w);
      }
      break;
    case 4:
      rt(t, e), gt(e);
      break;
    case 13:
      rt(t, e), gt(e), o = e.child, o.flags & 8192 && (i = o.memoizedState !== null, o.stateNode.isHidden = i, !i || o.alternate !== null && o.alternate.memoizedState !== null || (wa = ve())), r & 4 && ef(e);
      break;
    case 22:
      if (d = n !== null && n.memoizedState !== null, e.mode & 1 ? (Te = (a = Te) || d, rt(t, e), Te = a) : rt(t, e), gt(e), r & 8192) {
        if (a = e.memoizedState !== null, (e.stateNode.isHidden = a) && !d && e.mode & 1) for (Y = e, d = e.child; d !== null; ) {
          for (c = Y = d; Y !== null; ) {
            switch (f = Y, m = f.child, f.tag) {
              case 0:
              case 11:
              case 14:
              case 15:
                Gr(4, f, f.return);
                break;
              case 1:
                Qn(f, f.return);
                var y = f.stateNode;
                if (typeof y.componentWillUnmount == "function") {
                  r = f, n = f.return;
                  try {
                    t = r, y.props = t.memoizedProps, y.state = t.memoizedState, y.componentWillUnmount();
                  } catch (w) {
                    ye(r, n, w);
                  }
                }
                break;
              case 5:
                Qn(f, f.return);
                break;
              case 22:
                if (f.memoizedState !== null) {
                  nf(c);
                  continue;
                }
            }
            m !== null ? (m.return = f, Y = m) : nf(c);
          }
          d = d.sibling;
        }
        e: for (d = null, c = e; ; ) {
          if (c.tag === 5) {
            if (d === null) {
              d = c;
              try {
                o = c.stateNode, a ? (i = o.style, typeof i.setProperty == "function" ? i.setProperty("display", "none", "important") : i.display = "none") : (l = c.stateNode, u = c.memoizedProps.style, s = u != null && u.hasOwnProperty("display") ? u.display : null, l.style.display = Od("display", s));
              } catch (w) {
                ye(e, e.return, w);
              }
            }
          } else if (c.tag === 6) {
            if (d === null) try {
              c.stateNode.nodeValue = a ? "" : c.memoizedProps;
            } catch (w) {
              ye(e, e.return, w);
            }
          } else if ((c.tag !== 22 && c.tag !== 23 || c.memoizedState === null || c === e) && c.child !== null) {
            c.child.return = c, c = c.child;
            continue;
          }
          if (c === e) break e;
          for (; c.sibling === null; ) {
            if (c.return === null || c.return === e) break e;
            d === c && (d = null), c = c.return;
          }
          d === c && (d = null), c.sibling.return = c.return, c = c.sibling;
        }
      }
      break;
    case 19:
      rt(t, e), gt(e), r & 4 && ef(e);
      break;
    case 21:
      break;
    default:
      rt(
        t,
        e
      ), gt(e);
  }
}
function gt(e) {
  var t = e.flags;
  if (t & 2) {
    try {
      e: {
        for (var n = e.return; n !== null; ) {
          if (cp(n)) {
            var r = n;
            break e;
          }
          n = n.return;
        }
        throw Error(V(160));
      }
      switch (r.tag) {
        case 5:
          var o = r.stateNode;
          r.flags & 32 && (no(o, ""), r.flags &= -33);
          var i = Jc(e);
          hu(e, i, o);
          break;
        case 3:
        case 4:
          var s = r.stateNode.containerInfo, l = Jc(e);
          du(e, l, s);
          break;
        default:
          throw Error(V(161));
      }
    } catch (u) {
      ye(e, e.return, u);
    }
    e.flags &= -3;
  }
  t & 4096 && (e.flags &= -4097);
}
function zv(e, t, n) {
  Y = e, hp(e);
}
function hp(e, t, n) {
  for (var r = (e.mode & 1) !== 0; Y !== null; ) {
    var o = Y, i = o.child;
    if (o.tag === 22 && r) {
      var s = o.memoizedState !== null || ni;
      if (!s) {
        var l = o.alternate, u = l !== null && l.memoizedState !== null || Te;
        l = ni;
        var a = Te;
        if (ni = s, (Te = u) && !a) for (Y = o; Y !== null; ) s = Y, u = s.child, s.tag === 22 && s.memoizedState !== null ? rf(o) : u !== null ? (u.return = s, Y = u) : rf(o);
        for (; i !== null; ) Y = i, hp(i), i = i.sibling;
        Y = o, ni = l, Te = a;
      }
      tf(e);
    } else o.subtreeFlags & 8772 && i !== null ? (i.return = o, Y = i) : tf(e);
  }
}
function tf(e) {
  for (; Y !== null; ) {
    var t = Y;
    if (t.flags & 8772) {
      var n = t.alternate;
      try {
        if (t.flags & 8772) switch (t.tag) {
          case 0:
          case 11:
          case 15:
            Te || xs(5, t);
            break;
          case 1:
            var r = t.stateNode;
            if (t.flags & 4 && !Te) if (n === null) r.componentDidMount();
            else {
              var o = t.elementType === t.type ? n.memoizedProps : ot(t.type, n.memoizedProps);
              r.componentDidUpdate(o, n.memoizedState, r.__reactInternalSnapshotBeforeUpdate);
            }
            var i = t.updateQueue;
            i !== null && Fc(t, i, r);
            break;
          case 3:
            var s = t.updateQueue;
            if (s !== null) {
              if (n = null, t.child !== null) switch (t.child.tag) {
                case 5:
                  n = t.child.stateNode;
                  break;
                case 1:
                  n = t.child.stateNode;
              }
              Fc(t, s, n);
            }
            break;
          case 5:
            var l = t.stateNode;
            if (n === null && t.flags & 4) {
              n = l;
              var u = t.memoizedProps;
              switch (t.type) {
                case "button":
                case "input":
                case "select":
                case "textarea":
                  u.autoFocus && n.focus();
                  break;
                case "img":
                  u.src && (n.src = u.src);
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
              var a = t.alternate;
              if (a !== null) {
                var d = a.memoizedState;
                if (d !== null) {
                  var c = d.dehydrated;
                  c !== null && so(c);
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
            throw Error(V(163));
        }
        Te || t.flags & 512 && fu(t);
      } catch (f) {
        ye(t, t.return, f);
      }
    }
    if (t === e) {
      Y = null;
      break;
    }
    if (n = t.sibling, n !== null) {
      n.return = t.return, Y = n;
      break;
    }
    Y = t.return;
  }
}
function nf(e) {
  for (; Y !== null; ) {
    var t = Y;
    if (t === e) {
      Y = null;
      break;
    }
    var n = t.sibling;
    if (n !== null) {
      n.return = t.return, Y = n;
      break;
    }
    Y = t.return;
  }
}
function rf(e) {
  for (; Y !== null; ) {
    var t = Y;
    try {
      switch (t.tag) {
        case 0:
        case 11:
        case 15:
          var n = t.return;
          try {
            xs(4, t);
          } catch (u) {
            ye(t, n, u);
          }
          break;
        case 1:
          var r = t.stateNode;
          if (typeof r.componentDidMount == "function") {
            var o = t.return;
            try {
              r.componentDidMount();
            } catch (u) {
              ye(t, o, u);
            }
          }
          var i = t.return;
          try {
            fu(t);
          } catch (u) {
            ye(t, i, u);
          }
          break;
        case 5:
          var s = t.return;
          try {
            fu(t);
          } catch (u) {
            ye(t, s, u);
          }
      }
    } catch (u) {
      ye(t, t.return, u);
    }
    if (t === e) {
      Y = null;
      break;
    }
    var l = t.sibling;
    if (l !== null) {
      l.return = t.return, Y = l;
      break;
    }
    Y = t.return;
  }
}
var Iv = Math.ceil, Gi = jt.ReactCurrentDispatcher, ya = jt.ReactCurrentOwner, et = jt.ReactCurrentBatchConfig, re = 0, ke = null, we = null, Ce = 0, Ue = 0, bn = an(0), _e = 0, vo = null, Cn = 0, Ss = 0, va = 0, Zr = null, je = null, wa = 0, fr = 1 / 0, Ct = null, Zi = !1, pu = null, tn = null, ri = !1, Kt = null, qi = 0, qr = 0, gu = null, Ei = -1, ki = 0;
function $e() {
  return re & 6 ? ve() : Ei !== -1 ? Ei : Ei = ve();
}
function nn(e) {
  return e.mode & 1 ? re & 2 && Ce !== 0 ? Ce & -Ce : hv.transition !== null ? (ki === 0 && (ki = Zd()), ki) : (e = se, e !== 0 || (e = window.event, e = e === void 0 ? 16 : oh(e.type)), e) : 1;
}
function dt(e, t, n, r) {
  if (50 < qr) throw qr = 0, gu = null, Error(V(185));
  Io(e, n, r), (!(re & 2) || e !== ke) && (e === ke && (!(re & 2) && (Ss |= n), _e === 4 && Xt(e, Ce)), Be(e, r), n === 1 && re === 0 && !(t.mode & 1) && (fr = ve() + 500, ys && cn()));
}
function Be(e, t) {
  var n = e.callbackNode;
  hy(e, t);
  var r = Di(e, e === ke ? Ce : 0);
  if (r === 0) n !== null && dc(n), e.callbackNode = null, e.callbackPriority = 0;
  else if (t = r & -r, e.callbackPriority !== t) {
    if (n != null && dc(n), t === 1) e.tag === 0 ? dv(of.bind(null, e)) : Eh(of.bind(null, e)), uv(function() {
      !(re & 6) && cn();
    }), n = null;
    else {
      switch (qd(r)) {
        case 1:
          n = Yu;
          break;
        case 4:
          n = Kd;
          break;
        case 16:
          n = $i;
          break;
        case 536870912:
          n = Gd;
          break;
        default:
          n = $i;
      }
      n = Sp(n, pp.bind(null, e));
    }
    e.callbackPriority = t, e.callbackNode = n;
  }
}
function pp(e, t) {
  if (Ei = -1, ki = 0, re & 6) throw Error(V(327));
  var n = e.callbackNode;
  if (nr() && e.callbackNode !== n) return null;
  var r = Di(e, e === ke ? Ce : 0);
  if (r === 0) return null;
  if (r & 30 || r & e.expiredLanes || t) t = Ji(e, r);
  else {
    t = r;
    var o = re;
    re |= 2;
    var i = mp();
    (ke !== e || Ce !== t) && (Ct = null, fr = ve() + 500, wn(e, t));
    do
      try {
        Av();
        break;
      } catch (l) {
        gp(e, l);
      }
    while (!0);
    oa(), Gi.current = i, re = o, we !== null ? t = 0 : (ke = null, Ce = 0, t = _e);
  }
  if (t !== 0) {
    if (t === 2 && (o = Bl(e), o !== 0 && (r = o, t = mu(e, o))), t === 1) throw n = vo, wn(e, 0), Xt(e, r), Be(e, ve()), n;
    if (t === 6) Xt(e, r);
    else {
      if (o = e.current.alternate, !(r & 30) && !Tv(o) && (t = Ji(e, r), t === 2 && (i = Bl(e), i !== 0 && (r = i, t = mu(e, i))), t === 1)) throw n = vo, wn(e, 0), Xt(e, r), Be(e, ve()), n;
      switch (e.finishedWork = o, e.finishedLanes = r, t) {
        case 0:
        case 1:
          throw Error(V(345));
        case 2:
          hn(e, je, Ct);
          break;
        case 3:
          if (Xt(e, r), (r & 130023424) === r && (t = wa + 500 - ve(), 10 < t)) {
            if (Di(e, 0) !== 0) break;
            if (o = e.suspendedLanes, (o & r) !== r) {
              $e(), e.pingedLanes |= e.suspendedLanes & o;
              break;
            }
            e.timeoutHandle = Gl(hn.bind(null, e, je, Ct), t);
            break;
          }
          hn(e, je, Ct);
          break;
        case 4:
          if (Xt(e, r), (r & 4194240) === r) break;
          for (t = e.eventTimes, o = -1; 0 < r; ) {
            var s = 31 - ft(r);
            i = 1 << s, s = t[s], s > o && (o = s), r &= ~i;
          }
          if (r = o, r = ve() - r, r = (120 > r ? 120 : 480 > r ? 480 : 1080 > r ? 1080 : 1920 > r ? 1920 : 3e3 > r ? 3e3 : 4320 > r ? 4320 : 1960 * Iv(r / 1960)) - r, 10 < r) {
            e.timeoutHandle = Gl(hn.bind(null, e, je, Ct), r);
            break;
          }
          hn(e, je, Ct);
          break;
        case 5:
          hn(e, je, Ct);
          break;
        default:
          throw Error(V(329));
      }
    }
  }
  return Be(e, ve()), e.callbackNode === n ? pp.bind(null, e) : null;
}
function mu(e, t) {
  var n = Zr;
  return e.current.memoizedState.isDehydrated && (wn(e, t).flags |= 256), e = Ji(e, t), e !== 2 && (t = je, je = n, t !== null && yu(t)), e;
}
function yu(e) {
  je === null ? je = e : je.push.apply(je, e);
}
function Tv(e) {
  for (var t = e; ; ) {
    if (t.flags & 16384) {
      var n = t.updateQueue;
      if (n !== null && (n = n.stores, n !== null)) for (var r = 0; r < n.length; r++) {
        var o = n[r], i = o.getSnapshot;
        o = o.value;
        try {
          if (!ht(i(), o)) return !1;
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
function Xt(e, t) {
  for (t &= ~va, t &= ~Ss, e.suspendedLanes |= t, e.pingedLanes &= ~t, e = e.expirationTimes; 0 < t; ) {
    var n = 31 - ft(t), r = 1 << n;
    e[n] = -1, t &= ~r;
  }
}
function of(e) {
  if (re & 6) throw Error(V(327));
  nr();
  var t = Di(e, 0);
  if (!(t & 1)) return Be(e, ve()), null;
  var n = Ji(e, t);
  if (e.tag !== 0 && n === 2) {
    var r = Bl(e);
    r !== 0 && (t = r, n = mu(e, r));
  }
  if (n === 1) throw n = vo, wn(e, 0), Xt(e, t), Be(e, ve()), n;
  if (n === 6) throw Error(V(345));
  return e.finishedWork = e.current.alternate, e.finishedLanes = t, hn(e, je, Ct), Be(e, ve()), null;
}
function xa(e, t) {
  var n = re;
  re |= 1;
  try {
    return e(t);
  } finally {
    re = n, re === 0 && (fr = ve() + 500, ys && cn());
  }
}
function Mn(e) {
  Kt !== null && Kt.tag === 0 && !(re & 6) && nr();
  var t = re;
  re |= 1;
  var n = et.transition, r = se;
  try {
    if (et.transition = null, se = 1, e) return e();
  } finally {
    se = r, et.transition = n, re = t, !(re & 6) && cn();
  }
}
function Sa() {
  Ue = bn.current, ce(bn);
}
function wn(e, t) {
  e.finishedWork = null, e.finishedLanes = 0;
  var n = e.timeoutHandle;
  if (n !== -1 && (e.timeoutHandle = -1, lv(n)), we !== null) for (n = we.return; n !== null; ) {
    var r = n;
    switch (ta(r), r.tag) {
      case 1:
        r = r.type.childContextTypes, r != null && Hi();
        break;
      case 3:
        ar(), ce(He), ce(Le), ca();
        break;
      case 5:
        aa(r);
        break;
      case 4:
        ar();
        break;
      case 13:
        ce(pe);
        break;
      case 19:
        ce(pe);
        break;
      case 10:
        ia(r.type._context);
        break;
      case 22:
      case 23:
        Sa();
    }
    n = n.return;
  }
  if (ke = e, we = e = rn(e.current, null), Ce = Ue = t, _e = 0, vo = null, va = Ss = Cn = 0, je = Zr = null, mn !== null) {
    for (t = 0; t < mn.length; t++) if (n = mn[t], r = n.interleaved, r !== null) {
      n.interleaved = null;
      var o = r.next, i = n.pending;
      if (i !== null) {
        var s = i.next;
        i.next = o, r.next = s;
      }
      n.pending = r;
    }
    mn = null;
  }
  return e;
}
function gp(e, t) {
  do {
    var n = we;
    try {
      if (oa(), xi.current = Ki, bi) {
        for (var r = ge.memoizedState; r !== null; ) {
          var o = r.queue;
          o !== null && (o.pending = null), r = r.next;
        }
        bi = !1;
      }
      if (Nn = 0, Ee = Se = ge = null, Kr = !1, go = 0, ya.current = null, n === null || n.return === null) {
        _e = 1, vo = t, we = null;
        break;
      }
      e: {
        var i = e, s = n.return, l = n, u = t;
        if (t = Ce, l.flags |= 32768, u !== null && typeof u == "object" && typeof u.then == "function") {
          var a = u, d = l, c = d.tag;
          if (!(d.mode & 1) && (c === 0 || c === 11 || c === 15)) {
            var f = d.alternate;
            f ? (d.updateQueue = f.updateQueue, d.memoizedState = f.memoizedState, d.lanes = f.lanes) : (d.updateQueue = null, d.memoizedState = null);
          }
          var m = Yc(s);
          if (m !== null) {
            m.flags &= -257, Xc(m, s, l, i, t), m.mode & 1 && Wc(i, a, t), t = m, u = a;
            var y = t.updateQueue;
            if (y === null) {
              var w = /* @__PURE__ */ new Set();
              w.add(u), t.updateQueue = w;
            } else y.add(u);
            break e;
          } else {
            if (!(t & 1)) {
              Wc(i, a, t), _a();
              break e;
            }
            u = Error(V(426));
          }
        } else if (fe && l.mode & 1) {
          var x = Yc(s);
          if (x !== null) {
            !(x.flags & 65536) && (x.flags |= 256), Xc(x, s, l, i, t), na(cr(u, l));
            break e;
          }
        }
        i = u = cr(u, l), _e !== 4 && (_e = 2), Zr === null ? Zr = [i] : Zr.push(i), i = s;
        do {
          switch (i.tag) {
            case 3:
              i.flags |= 65536, t &= -t, i.lanes |= t;
              var p = qh(i, u, t);
              Oc(i, p);
              break e;
            case 1:
              l = u;
              var g = i.type, h = i.stateNode;
              if (!(i.flags & 128) && (typeof g.getDerivedStateFromError == "function" || h !== null && typeof h.componentDidCatch == "function" && (tn === null || !tn.has(h)))) {
                i.flags |= 65536, t &= -t, i.lanes |= t;
                var v = Jh(i, l, t);
                Oc(i, v);
                break e;
              }
          }
          i = i.return;
        } while (i !== null);
      }
      vp(n);
    } catch (S) {
      t = S, we === n && n !== null && (we = n = n.return);
      continue;
    }
    break;
  } while (!0);
}
function mp() {
  var e = Gi.current;
  return Gi.current = Ki, e === null ? Ki : e;
}
function _a() {
  (_e === 0 || _e === 3 || _e === 2) && (_e = 4), ke === null || !(Cn & 268435455) && !(Ss & 268435455) || Xt(ke, Ce);
}
function Ji(e, t) {
  var n = re;
  re |= 2;
  var r = mp();
  (ke !== e || Ce !== t) && (Ct = null, wn(e, t));
  do
    try {
      Lv();
      break;
    } catch (o) {
      gp(e, o);
    }
  while (!0);
  if (oa(), re = n, Gi.current = r, we !== null) throw Error(V(261));
  return ke = null, Ce = 0, _e;
}
function Lv() {
  for (; we !== null; ) yp(we);
}
function Av() {
  for (; we !== null && !oy(); ) yp(we);
}
function yp(e) {
  var t = xp(e.alternate, e, Ue);
  e.memoizedProps = e.pendingProps, t === null ? vp(e) : we = t, ya.current = null;
}
function vp(e) {
  var t = e;
  do {
    var n = t.alternate;
    if (e = t.return, t.flags & 32768) {
      if (n = Cv(n, t), n !== null) {
        n.flags &= 32767, we = n;
        return;
      }
      if (e !== null) e.flags |= 32768, e.subtreeFlags = 0, e.deletions = null;
      else {
        _e = 6, we = null;
        return;
      }
    } else if (n = Nv(n, t, Ue), n !== null) {
      we = n;
      return;
    }
    if (t = t.sibling, t !== null) {
      we = t;
      return;
    }
    we = t = e;
  } while (t !== null);
  _e === 0 && (_e = 5);
}
function hn(e, t, n) {
  var r = se, o = et.transition;
  try {
    et.transition = null, se = 1, $v(e, t, n, r);
  } finally {
    et.transition = o, se = r;
  }
  return null;
}
function $v(e, t, n, r) {
  do
    nr();
  while (Kt !== null);
  if (re & 6) throw Error(V(327));
  n = e.finishedWork;
  var o = e.finishedLanes;
  if (n === null) return null;
  if (e.finishedWork = null, e.finishedLanes = 0, n === e.current) throw Error(V(177));
  e.callbackNode = null, e.callbackPriority = 0;
  var i = n.lanes | n.childLanes;
  if (py(e, i), e === ke && (we = ke = null, Ce = 0), !(n.subtreeFlags & 2064) && !(n.flags & 2064) || ri || (ri = !0, Sp($i, function() {
    return nr(), null;
  })), i = (n.flags & 15990) !== 0, n.subtreeFlags & 15990 || i) {
    i = et.transition, et.transition = null;
    var s = se;
    se = 1;
    var l = re;
    re |= 4, ya.current = null, Pv(e, n), dp(n, e), ev(bl), Ri = !!Ql, bl = Ql = null, e.current = n, zv(n), iy(), re = l, se = s, et.transition = i;
  } else e.current = n;
  if (ri && (ri = !1, Kt = e, qi = o), i = e.pendingLanes, i === 0 && (tn = null), uy(n.stateNode), Be(e, ve()), t !== null) for (r = e.onRecoverableError, n = 0; n < t.length; n++) o = t[n], r(o.value, { componentStack: o.stack, digest: o.digest });
  if (Zi) throw Zi = !1, e = pu, pu = null, e;
  return qi & 1 && e.tag !== 0 && nr(), i = e.pendingLanes, i & 1 ? e === gu ? qr++ : (qr = 0, gu = e) : qr = 0, cn(), null;
}
function nr() {
  if (Kt !== null) {
    var e = qd(qi), t = et.transition, n = se;
    try {
      if (et.transition = null, se = 16 > e ? 16 : e, Kt === null) var r = !1;
      else {
        if (e = Kt, Kt = null, qi = 0, re & 6) throw Error(V(331));
        var o = re;
        for (re |= 4, Y = e.current; Y !== null; ) {
          var i = Y, s = i.child;
          if (Y.flags & 16) {
            var l = i.deletions;
            if (l !== null) {
              for (var u = 0; u < l.length; u++) {
                var a = l[u];
                for (Y = a; Y !== null; ) {
                  var d = Y;
                  switch (d.tag) {
                    case 0:
                    case 11:
                    case 15:
                      Gr(8, d, i);
                  }
                  var c = d.child;
                  if (c !== null) c.return = d, Y = c;
                  else for (; Y !== null; ) {
                    d = Y;
                    var f = d.sibling, m = d.return;
                    if (ap(d), d === a) {
                      Y = null;
                      break;
                    }
                    if (f !== null) {
                      f.return = m, Y = f;
                      break;
                    }
                    Y = m;
                  }
                }
              }
              var y = i.alternate;
              if (y !== null) {
                var w = y.child;
                if (w !== null) {
                  y.child = null;
                  do {
                    var x = w.sibling;
                    w.sibling = null, w = x;
                  } while (w !== null);
                }
              }
              Y = i;
            }
          }
          if (i.subtreeFlags & 2064 && s !== null) s.return = i, Y = s;
          else e: for (; Y !== null; ) {
            if (i = Y, i.flags & 2048) switch (i.tag) {
              case 0:
              case 11:
              case 15:
                Gr(9, i, i.return);
            }
            var p = i.sibling;
            if (p !== null) {
              p.return = i.return, Y = p;
              break e;
            }
            Y = i.return;
          }
        }
        var g = e.current;
        for (Y = g; Y !== null; ) {
          s = Y;
          var h = s.child;
          if (s.subtreeFlags & 2064 && h !== null) h.return = s, Y = h;
          else e: for (s = g; Y !== null; ) {
            if (l = Y, l.flags & 2048) try {
              switch (l.tag) {
                case 0:
                case 11:
                case 15:
                  xs(9, l);
              }
            } catch (S) {
              ye(l, l.return, S);
            }
            if (l === s) {
              Y = null;
              break e;
            }
            var v = l.sibling;
            if (v !== null) {
              v.return = l.return, Y = v;
              break e;
            }
            Y = l.return;
          }
        }
        if (re = o, cn(), wt && typeof wt.onPostCommitFiberRoot == "function") try {
          wt.onPostCommitFiberRoot(ds, e);
        } catch {
        }
        r = !0;
      }
      return r;
    } finally {
      se = n, et.transition = t;
    }
  }
  return !1;
}
function sf(e, t, n) {
  t = cr(n, t), t = qh(e, t, 1), e = en(e, t, 1), t = $e(), e !== null && (Io(e, 1, t), Be(e, t));
}
function ye(e, t, n) {
  if (e.tag === 3) sf(e, e, n);
  else for (; t !== null; ) {
    if (t.tag === 3) {
      sf(t, e, n);
      break;
    } else if (t.tag === 1) {
      var r = t.stateNode;
      if (typeof t.type.getDerivedStateFromError == "function" || typeof r.componentDidCatch == "function" && (tn === null || !tn.has(r))) {
        e = cr(n, e), e = Jh(t, e, 1), t = en(t, e, 1), e = $e(), t !== null && (Io(t, 1, e), Be(t, e));
        break;
      }
    }
    t = t.return;
  }
}
function Dv(e, t, n) {
  var r = e.pingCache;
  r !== null && r.delete(t), t = $e(), e.pingedLanes |= e.suspendedLanes & n, ke === e && (Ce & n) === n && (_e === 4 || _e === 3 && (Ce & 130023424) === Ce && 500 > ve() - wa ? wn(e, 0) : va |= n), Be(e, t);
}
function wp(e, t) {
  t === 0 && (e.mode & 1 ? (t = Qo, Qo <<= 1, !(Qo & 130023424) && (Qo = 4194304)) : t = 1);
  var n = $e();
  e = $t(e, t), e !== null && (Io(e, t, n), Be(e, n));
}
function Rv(e) {
  var t = e.memoizedState, n = 0;
  t !== null && (n = t.retryLane), wp(e, n);
}
function jv(e, t) {
  var n = 0;
  switch (e.tag) {
    case 13:
      var r = e.stateNode, o = e.memoizedState;
      o !== null && (n = o.retryLane);
      break;
    case 19:
      r = e.stateNode;
      break;
    default:
      throw Error(V(314));
  }
  r !== null && r.delete(t), wp(e, n);
}
var xp;
xp = function(e, t, n) {
  if (e !== null) if (e.memoizedProps !== t.pendingProps || He.current) Oe = !0;
  else {
    if (!(e.lanes & n) && !(t.flags & 128)) return Oe = !1, kv(e, t, n);
    Oe = !!(e.flags & 131072);
  }
  else Oe = !1, fe && t.flags & 1048576 && kh(t, Ui, t.index);
  switch (t.lanes = 0, t.tag) {
    case 2:
      var r = t.type;
      _i(e, t), e = t.pendingProps;
      var o = sr(t, Le.current);
      tr(t, n), o = da(null, t, r, e, o, n);
      var i = ha();
      return t.flags |= 1, typeof o == "object" && o !== null && typeof o.render == "function" && o.$$typeof === void 0 ? (t.tag = 1, t.memoizedState = null, t.updateQueue = null, Ve(r) ? (i = !0, Vi(t)) : i = !1, t.memoizedState = o.state !== null && o.state !== void 0 ? o.state : null, la(t), o.updater = ws, t.stateNode = o, o._reactInternals = t, ru(t, r, e, n), t = su(null, t, r, !0, i, n)) : (t.tag = 0, fe && i && ea(t), Ae(null, t, o, n), t = t.child), t;
    case 16:
      r = t.elementType;
      e: {
        switch (_i(e, t), e = t.pendingProps, o = r._init, r = o(r._payload), t.type = r, o = t.tag = Fv(r), e = ot(r, e), o) {
          case 0:
            t = iu(null, t, r, e, n);
            break e;
          case 1:
            t = Kc(null, t, r, e, n);
            break e;
          case 11:
            t = Qc(null, t, r, e, n);
            break e;
          case 14:
            t = bc(null, t, r, ot(r.type, e), n);
            break e;
        }
        throw Error(V(
          306,
          r,
          ""
        ));
      }
      return t;
    case 0:
      return r = t.type, o = t.pendingProps, o = t.elementType === r ? o : ot(r, o), iu(e, t, r, o, n);
    case 1:
      return r = t.type, o = t.pendingProps, o = t.elementType === r ? o : ot(r, o), Kc(e, t, r, o, n);
    case 3:
      e: {
        if (rp(t), e === null) throw Error(V(387));
        r = t.pendingProps, i = t.memoizedState, o = i.element, Ih(e, t), Xi(t, r, null, n);
        var s = t.memoizedState;
        if (r = s.element, i.isDehydrated) if (i = { element: r, isDehydrated: !1, cache: s.cache, pendingSuspenseBoundaries: s.pendingSuspenseBoundaries, transitions: s.transitions }, t.updateQueue.baseState = i, t.memoizedState = i, t.flags & 256) {
          o = cr(Error(V(423)), t), t = Gc(e, t, r, n, o);
          break e;
        } else if (r !== o) {
          o = cr(Error(V(424)), t), t = Gc(e, t, r, n, o);
          break e;
        } else for (Ye = Jt(t.stateNode.containerInfo.firstChild), Xe = t, fe = !0, lt = null, n = Ph(t, null, r, n), t.child = n; n; ) n.flags = n.flags & -3 | 4096, n = n.sibling;
        else {
          if (lr(), r === o) {
            t = Dt(e, t, n);
            break e;
          }
          Ae(e, t, r, n);
        }
        t = t.child;
      }
      return t;
    case 5:
      return Th(t), e === null && eu(t), r = t.type, o = t.pendingProps, i = e !== null ? e.memoizedProps : null, s = o.children, Kl(r, o) ? s = null : i !== null && Kl(r, i) && (t.flags |= 32), np(e, t), Ae(e, t, s, n), t.child;
    case 6:
      return e === null && eu(t), null;
    case 13:
      return op(e, t, n);
    case 4:
      return ua(t, t.stateNode.containerInfo), r = t.pendingProps, e === null ? t.child = ur(t, null, r, n) : Ae(e, t, r, n), t.child;
    case 11:
      return r = t.type, o = t.pendingProps, o = t.elementType === r ? o : ot(r, o), Qc(e, t, r, o, n);
    case 7:
      return Ae(e, t, t.pendingProps, n), t.child;
    case 8:
      return Ae(e, t, t.pendingProps.children, n), t.child;
    case 12:
      return Ae(e, t, t.pendingProps.children, n), t.child;
    case 10:
      e: {
        if (r = t.type._context, o = t.pendingProps, i = t.memoizedProps, s = o.value, le(Wi, r._currentValue), r._currentValue = s, i !== null) if (ht(i.value, s)) {
          if (i.children === o.children && !He.current) {
            t = Dt(e, t, n);
            break e;
          }
        } else for (i = t.child, i !== null && (i.return = t); i !== null; ) {
          var l = i.dependencies;
          if (l !== null) {
            s = i.child;
            for (var u = l.firstContext; u !== null; ) {
              if (u.context === r) {
                if (i.tag === 1) {
                  u = Tt(-1, n & -n), u.tag = 2;
                  var a = i.updateQueue;
                  if (a !== null) {
                    a = a.shared;
                    var d = a.pending;
                    d === null ? u.next = u : (u.next = d.next, d.next = u), a.pending = u;
                  }
                }
                i.lanes |= n, u = i.alternate, u !== null && (u.lanes |= n), tu(
                  i.return,
                  n,
                  t
                ), l.lanes |= n;
                break;
              }
              u = u.next;
            }
          } else if (i.tag === 10) s = i.type === t.type ? null : i.child;
          else if (i.tag === 18) {
            if (s = i.return, s === null) throw Error(V(341));
            s.lanes |= n, l = s.alternate, l !== null && (l.lanes |= n), tu(s, n, t), s = i.sibling;
          } else s = i.child;
          if (s !== null) s.return = i;
          else for (s = i; s !== null; ) {
            if (s === t) {
              s = null;
              break;
            }
            if (i = s.sibling, i !== null) {
              i.return = s.return, s = i;
              break;
            }
            s = s.return;
          }
          i = s;
        }
        Ae(e, t, o.children, n), t = t.child;
      }
      return t;
    case 9:
      return o = t.type, r = t.pendingProps.children, tr(t, n), o = tt(o), r = r(o), t.flags |= 1, Ae(e, t, r, n), t.child;
    case 14:
      return r = t.type, o = ot(r, t.pendingProps), o = ot(r.type, o), bc(e, t, r, o, n);
    case 15:
      return ep(e, t, t.type, t.pendingProps, n);
    case 17:
      return r = t.type, o = t.pendingProps, o = t.elementType === r ? o : ot(r, o), _i(e, t), t.tag = 1, Ve(r) ? (e = !0, Vi(t)) : e = !1, tr(t, n), Zh(t, r, o), ru(t, r, o, n), su(null, t, r, !0, e, n);
    case 19:
      return ip(e, t, n);
    case 22:
      return tp(e, t, n);
  }
  throw Error(V(156, t.tag));
};
function Sp(e, t) {
  return bd(e, t);
}
function Ov(e, t, n, r) {
  this.tag = e, this.key = n, this.sibling = this.child = this.return = this.stateNode = this.type = this.elementType = null, this.index = 0, this.ref = null, this.pendingProps = t, this.dependencies = this.memoizedState = this.updateQueue = this.memoizedProps = null, this.mode = r, this.subtreeFlags = this.flags = 0, this.deletions = null, this.childLanes = this.lanes = 0, this.alternate = null;
}
function Je(e, t, n, r) {
  return new Ov(e, t, n, r);
}
function Ea(e) {
  return e = e.prototype, !(!e || !e.isReactComponent);
}
function Fv(e) {
  if (typeof e == "function") return Ea(e) ? 1 : 0;
  if (e != null) {
    if (e = e.$$typeof, e === Bu) return 11;
    if (e === Uu) return 14;
  }
  return 2;
}
function rn(e, t) {
  var n = e.alternate;
  return n === null ? (n = Je(e.tag, t, e.key, e.mode), n.elementType = e.elementType, n.type = e.type, n.stateNode = e.stateNode, n.alternate = e, e.alternate = n) : (n.pendingProps = t, n.type = e.type, n.flags = 0, n.subtreeFlags = 0, n.deletions = null), n.flags = e.flags & 14680064, n.childLanes = e.childLanes, n.lanes = e.lanes, n.child = e.child, n.memoizedProps = e.memoizedProps, n.memoizedState = e.memoizedState, n.updateQueue = e.updateQueue, t = e.dependencies, n.dependencies = t === null ? null : { lanes: t.lanes, firstContext: t.firstContext }, n.sibling = e.sibling, n.index = e.index, n.ref = e.ref, n;
}
function Ni(e, t, n, r, o, i) {
  var s = 2;
  if (r = e, typeof e == "function") Ea(e) && (s = 1);
  else if (typeof e == "string") s = 5;
  else e: switch (e) {
    case On:
      return xn(n.children, o, i, t);
    case Vu:
      s = 8, o |= 8;
      break;
    case Ml:
      return e = Je(12, n, t, o | 2), e.elementType = Ml, e.lanes = i, e;
    case Pl:
      return e = Je(13, n, t, o), e.elementType = Pl, e.lanes = i, e;
    case zl:
      return e = Je(19, n, t, o), e.elementType = zl, e.lanes = i, e;
    case Td:
      return _s(n, o, i, t);
    default:
      if (typeof e == "object" && e !== null) switch (e.$$typeof) {
        case zd:
          s = 10;
          break e;
        case Id:
          s = 9;
          break e;
        case Bu:
          s = 11;
          break e;
        case Uu:
          s = 14;
          break e;
        case Ut:
          s = 16, r = null;
          break e;
      }
      throw Error(V(130, e == null ? e : typeof e, ""));
  }
  return t = Je(s, n, t, o), t.elementType = e, t.type = r, t.lanes = i, t;
}
function xn(e, t, n, r) {
  return e = Je(7, e, r, t), e.lanes = n, e;
}
function _s(e, t, n, r) {
  return e = Je(22, e, r, t), e.elementType = Td, e.lanes = n, e.stateNode = { isHidden: !1 }, e;
}
function pl(e, t, n) {
  return e = Je(6, e, null, t), e.lanes = n, e;
}
function gl(e, t, n) {
  return t = Je(4, e.children !== null ? e.children : [], e.key, t), t.lanes = n, t.stateNode = { containerInfo: e.containerInfo, pendingChildren: null, implementation: e.implementation }, t;
}
function Hv(e, t, n, r, o) {
  this.tag = t, this.containerInfo = e, this.finishedWork = this.pingCache = this.current = this.pendingChildren = null, this.timeoutHandle = -1, this.callbackNode = this.pendingContext = this.context = null, this.callbackPriority = 0, this.eventTimes = Ks(0), this.expirationTimes = Ks(-1), this.entangledLanes = this.finishedLanes = this.mutableReadLanes = this.expiredLanes = this.pingedLanes = this.suspendedLanes = this.pendingLanes = 0, this.entanglements = Ks(0), this.identifierPrefix = r, this.onRecoverableError = o, this.mutableSourceEagerHydrationData = null;
}
function ka(e, t, n, r, o, i, s, l, u) {
  return e = new Hv(e, t, n, l, u), t === 1 ? (t = 1, i === !0 && (t |= 8)) : t = 0, i = Je(3, null, null, t), e.current = i, i.stateNode = e, i.memoizedState = { element: r, isDehydrated: n, cache: null, transitions: null, pendingSuspenseBoundaries: null }, la(i), e;
}
function Vv(e, t, n) {
  var r = 3 < arguments.length && arguments[3] !== void 0 ? arguments[3] : null;
  return { $$typeof: jn, key: r == null ? null : "" + r, children: e, containerInfo: t, implementation: n };
}
function _p(e) {
  if (!e) return ln;
  e = e._reactInternals;
  e: {
    if (An(e) !== e || e.tag !== 1) throw Error(V(170));
    var t = e;
    do {
      switch (t.tag) {
        case 3:
          t = t.stateNode.context;
          break e;
        case 1:
          if (Ve(t.type)) {
            t = t.stateNode.__reactInternalMemoizedMergedChildContext;
            break e;
          }
      }
      t = t.return;
    } while (t !== null);
    throw Error(V(171));
  }
  if (e.tag === 1) {
    var n = e.type;
    if (Ve(n)) return _h(e, n, t);
  }
  return t;
}
function Ep(e, t, n, r, o, i, s, l, u) {
  return e = ka(n, r, !0, e, o, i, s, l, u), e.context = _p(null), n = e.current, r = $e(), o = nn(n), i = Tt(r, o), i.callback = t ?? null, en(n, i, o), e.current.lanes = o, Io(e, o, r), Be(e, r), e;
}
function Es(e, t, n, r) {
  var o = t.current, i = $e(), s = nn(o);
  return n = _p(n), t.context === null ? t.context = n : t.pendingContext = n, t = Tt(i, s), t.payload = { element: e }, r = r === void 0 ? null : r, r !== null && (t.callback = r), e = en(o, t, s), e !== null && (dt(e, o, s, i), wi(e, o, s)), s;
}
function es(e) {
  if (e = e.current, !e.child) return null;
  switch (e.child.tag) {
    case 5:
      return e.child.stateNode;
    default:
      return e.child.stateNode;
  }
}
function lf(e, t) {
  if (e = e.memoizedState, e !== null && e.dehydrated !== null) {
    var n = e.retryLane;
    e.retryLane = n !== 0 && n < t ? n : t;
  }
}
function Na(e, t) {
  lf(e, t), (e = e.alternate) && lf(e, t);
}
function Bv() {
  return null;
}
var kp = typeof reportError == "function" ? reportError : function(e) {
  console.error(e);
};
function Ca(e) {
  this._internalRoot = e;
}
ks.prototype.render = Ca.prototype.render = function(e) {
  var t = this._internalRoot;
  if (t === null) throw Error(V(409));
  Es(e, t, null, null);
};
ks.prototype.unmount = Ca.prototype.unmount = function() {
  var e = this._internalRoot;
  if (e !== null) {
    this._internalRoot = null;
    var t = e.containerInfo;
    Mn(function() {
      Es(null, e, null, null);
    }), t[At] = null;
  }
};
function ks(e) {
  this._internalRoot = e;
}
ks.prototype.unstable_scheduleHydration = function(e) {
  if (e) {
    var t = th();
    e = { blockedOn: null, target: e, priority: t };
    for (var n = 0; n < Yt.length && t !== 0 && t < Yt[n].priority; n++) ;
    Yt.splice(n, 0, e), n === 0 && rh(e);
  }
};
function Ma(e) {
  return !(!e || e.nodeType !== 1 && e.nodeType !== 9 && e.nodeType !== 11);
}
function Ns(e) {
  return !(!e || e.nodeType !== 1 && e.nodeType !== 9 && e.nodeType !== 11 && (e.nodeType !== 8 || e.nodeValue !== " react-mount-point-unstable "));
}
function uf() {
}
function Uv(e, t, n, r, o) {
  if (o) {
    if (typeof r == "function") {
      var i = r;
      r = function() {
        var a = es(s);
        i.call(a);
      };
    }
    var s = Ep(t, r, e, 0, null, !1, !1, "", uf);
    return e._reactRootContainer = s, e[At] = s.current, ao(e.nodeType === 8 ? e.parentNode : e), Mn(), s;
  }
  for (; o = e.lastChild; ) e.removeChild(o);
  if (typeof r == "function") {
    var l = r;
    r = function() {
      var a = es(u);
      l.call(a);
    };
  }
  var u = ka(e, 0, !1, null, null, !1, !1, "", uf);
  return e._reactRootContainer = u, e[At] = u.current, ao(e.nodeType === 8 ? e.parentNode : e), Mn(function() {
    Es(t, u, n, r);
  }), u;
}
function Cs(e, t, n, r, o) {
  var i = n._reactRootContainer;
  if (i) {
    var s = i;
    if (typeof o == "function") {
      var l = o;
      o = function() {
        var u = es(s);
        l.call(u);
      };
    }
    Es(t, s, e, o);
  } else s = Uv(n, t, e, o, r);
  return es(s);
}
Jd = function(e) {
  switch (e.tag) {
    case 3:
      var t = e.stateNode;
      if (t.current.memoizedState.isDehydrated) {
        var n = Hr(t.pendingLanes);
        n !== 0 && (Xu(t, n | 1), Be(t, ve()), !(re & 6) && (fr = ve() + 500, cn()));
      }
      break;
    case 13:
      Mn(function() {
        var r = $t(e, 1);
        if (r !== null) {
          var o = $e();
          dt(r, e, 1, o);
        }
      }), Na(e, 1);
  }
};
Qu = function(e) {
  if (e.tag === 13) {
    var t = $t(e, 134217728);
    if (t !== null) {
      var n = $e();
      dt(t, e, 134217728, n);
    }
    Na(e, 134217728);
  }
};
eh = function(e) {
  if (e.tag === 13) {
    var t = nn(e), n = $t(e, t);
    if (n !== null) {
      var r = $e();
      dt(n, e, t, r);
    }
    Na(e, t);
  }
};
th = function() {
  return se;
};
nh = function(e, t) {
  var n = se;
  try {
    return se = e, t();
  } finally {
    se = n;
  }
};
Fl = function(e, t, n) {
  switch (t) {
    case "input":
      if (Ll(e, n), t = n.name, n.type === "radio" && t != null) {
        for (n = e; n.parentNode; ) n = n.parentNode;
        for (n = n.querySelectorAll("input[name=" + JSON.stringify("" + t) + '][type="radio"]'), t = 0; t < n.length; t++) {
          var r = n[t];
          if (r !== e && r.form === e.form) {
            var o = ms(r);
            if (!o) throw Error(V(90));
            Ad(r), Ll(r, o);
          }
        }
      }
      break;
    case "textarea":
      Dd(e, n);
      break;
    case "select":
      t = n.value, t != null && Zn(e, !!n.multiple, t, !1);
  }
};
Bd = xa;
Ud = Mn;
var Wv = { usingClientEntryPoint: !1, Events: [Lo, Bn, ms, Hd, Vd, xa] }, Dr = { findFiberByHostInstance: gn, bundleType: 0, version: "18.3.1", rendererPackageName: "react-dom" }, Yv = { bundleType: Dr.bundleType, version: Dr.version, rendererPackageName: Dr.rendererPackageName, rendererConfig: Dr.rendererConfig, overrideHookState: null, overrideHookStateDeletePath: null, overrideHookStateRenamePath: null, overrideProps: null, overridePropsDeletePath: null, overridePropsRenamePath: null, setErrorHandler: null, setSuspenseHandler: null, scheduleUpdate: null, currentDispatcherRef: jt.ReactCurrentDispatcher, findHostInstanceByFiber: function(e) {
  return e = Xd(e), e === null ? null : e.stateNode;
}, findFiberByHostInstance: Dr.findFiberByHostInstance || Bv, findHostInstancesForRefresh: null, scheduleRefresh: null, scheduleRoot: null, setRefreshHandler: null, getCurrentFiber: null, reconcilerVersion: "18.3.1-next-f1338f8080-20240426" };
if (typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ < "u") {
  var oi = __REACT_DEVTOOLS_GLOBAL_HOOK__;
  if (!oi.isDisabled && oi.supportsFiber) try {
    ds = oi.inject(Yv), wt = oi;
  } catch {
  }
}
Ke.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED = Wv;
Ke.createPortal = function(e, t) {
  var n = 2 < arguments.length && arguments[2] !== void 0 ? arguments[2] : null;
  if (!Ma(t)) throw Error(V(200));
  return Vv(e, t, null, n);
};
Ke.createRoot = function(e, t) {
  if (!Ma(e)) throw Error(V(299));
  var n = !1, r = "", o = kp;
  return t != null && (t.unstable_strictMode === !0 && (n = !0), t.identifierPrefix !== void 0 && (r = t.identifierPrefix), t.onRecoverableError !== void 0 && (o = t.onRecoverableError)), t = ka(e, 1, !1, null, null, n, !1, r, o), e[At] = t.current, ao(e.nodeType === 8 ? e.parentNode : e), new Ca(t);
};
Ke.findDOMNode = function(e) {
  if (e == null) return null;
  if (e.nodeType === 1) return e;
  var t = e._reactInternals;
  if (t === void 0)
    throw typeof e.render == "function" ? Error(V(188)) : (e = Object.keys(e).join(","), Error(V(268, e)));
  return e = Xd(t), e = e === null ? null : e.stateNode, e;
};
Ke.flushSync = function(e) {
  return Mn(e);
};
Ke.hydrate = function(e, t, n) {
  if (!Ns(t)) throw Error(V(200));
  return Cs(null, e, t, !0, n);
};
Ke.hydrateRoot = function(e, t, n) {
  if (!Ma(e)) throw Error(V(405));
  var r = n != null && n.hydratedSources || null, o = !1, i = "", s = kp;
  if (n != null && (n.unstable_strictMode === !0 && (o = !0), n.identifierPrefix !== void 0 && (i = n.identifierPrefix), n.onRecoverableError !== void 0 && (s = n.onRecoverableError)), t = Ep(t, null, e, 1, n ?? null, o, !1, i, s), e[At] = t.current, ao(e), r) for (e = 0; e < r.length; e++) n = r[e], o = n._getVersion, o = o(n._source), t.mutableSourceEagerHydrationData == null ? t.mutableSourceEagerHydrationData = [n, o] : t.mutableSourceEagerHydrationData.push(
    n,
    o
  );
  return new ks(t);
};
Ke.render = function(e, t, n) {
  if (!Ns(t)) throw Error(V(200));
  return Cs(null, e, t, !1, n);
};
Ke.unmountComponentAtNode = function(e) {
  if (!Ns(e)) throw Error(V(40));
  return e._reactRootContainer ? (Mn(function() {
    Cs(null, null, e, !1, function() {
      e._reactRootContainer = null, e[At] = null;
    });
  }), !0) : !1;
};
Ke.unstable_batchedUpdates = xa;
Ke.unstable_renderSubtreeIntoContainer = function(e, t, n, r) {
  if (!Ns(n)) throw Error(V(200));
  if (e == null || e._reactInternals === void 0) throw Error(V(38));
  return Cs(e, t, n, !1, r);
};
Ke.version = "18.3.1-next-f1338f8080-20240426";
function Np() {
  if (!(typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ > "u" || typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE != "function"))
    try {
      __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE(Np);
    } catch (e) {
      console.error(e);
    }
}
Np(), Nd.exports = Ke;
var Xv = Nd.exports, Cp, af = Xv;
Cp = af.createRoot, af.hydrateRoot;
function xe(e) {
  if (typeof e == "string" || typeof e == "number") return "" + e;
  let t = "";
  if (Array.isArray(e))
    for (let n = 0, r; n < e.length; n++)
      (r = xe(e[n])) !== "" && (t += (t && " ") + r);
  else
    for (let n in e)
      e[n] && (t += (t && " ") + n);
  return t;
}
var Qv = { value: () => {
} };
function Ms() {
  for (var e = 0, t = arguments.length, n = {}, r; e < t; ++e) {
    if (!(r = arguments[e] + "") || r in n || /[\s.]/.test(r)) throw new Error("illegal type: " + r);
    n[r] = [];
  }
  return new Ci(n);
}
function Ci(e) {
  this._ = e;
}
function bv(e, t) {
  return e.trim().split(/^|\s+/).map(function(n) {
    var r = "", o = n.indexOf(".");
    if (o >= 0 && (r = n.slice(o + 1), n = n.slice(0, o)), n && !t.hasOwnProperty(n)) throw new Error("unknown type: " + n);
    return { type: n, name: r };
  });
}
Ci.prototype = Ms.prototype = {
  constructor: Ci,
  on: function(e, t) {
    var n = this._, r = bv(e + "", n), o, i = -1, s = r.length;
    if (arguments.length < 2) {
      for (; ++i < s; ) if ((o = (e = r[i]).type) && (o = Kv(n[o], e.name))) return o;
      return;
    }
    if (t != null && typeof t != "function") throw new Error("invalid callback: " + t);
    for (; ++i < s; )
      if (o = (e = r[i]).type) n[o] = cf(n[o], e.name, t);
      else if (t == null) for (o in n) n[o] = cf(n[o], e.name, null);
    return this;
  },
  copy: function() {
    var e = {}, t = this._;
    for (var n in t) e[n] = t[n].slice();
    return new Ci(e);
  },
  call: function(e, t) {
    if ((o = arguments.length - 2) > 0) for (var n = new Array(o), r = 0, o, i; r < o; ++r) n[r] = arguments[r + 2];
    if (!this._.hasOwnProperty(e)) throw new Error("unknown type: " + e);
    for (i = this._[e], r = 0, o = i.length; r < o; ++r) i[r].value.apply(t, n);
  },
  apply: function(e, t, n) {
    if (!this._.hasOwnProperty(e)) throw new Error("unknown type: " + e);
    for (var r = this._[e], o = 0, i = r.length; o < i; ++o) r[o].value.apply(t, n);
  }
};
function Kv(e, t) {
  for (var n = 0, r = e.length, o; n < r; ++n)
    if ((o = e[n]).name === t)
      return o.value;
}
function cf(e, t, n) {
  for (var r = 0, o = e.length; r < o; ++r)
    if (e[r].name === t) {
      e[r] = Qv, e = e.slice(0, r).concat(e.slice(r + 1));
      break;
    }
  return n != null && e.push({ name: t, value: n }), e;
}
var vu = "http://www.w3.org/1999/xhtml";
const ff = {
  svg: "http://www.w3.org/2000/svg",
  xhtml: vu,
  xlink: "http://www.w3.org/1999/xlink",
  xml: "http://www.w3.org/XML/1998/namespace",
  xmlns: "http://www.w3.org/2000/xmlns/"
};
function Ps(e) {
  var t = e += "", n = t.indexOf(":");
  return n >= 0 && (t = e.slice(0, n)) !== "xmlns" && (e = e.slice(n + 1)), ff.hasOwnProperty(t) ? { space: ff[t], local: e } : e;
}
function Gv(e) {
  return function() {
    var t = this.ownerDocument, n = this.namespaceURI;
    return n === vu && t.documentElement.namespaceURI === vu ? t.createElement(e) : t.createElementNS(n, e);
  };
}
function Zv(e) {
  return function() {
    return this.ownerDocument.createElementNS(e.space, e.local);
  };
}
function Mp(e) {
  var t = Ps(e);
  return (t.local ? Zv : Gv)(t);
}
function qv() {
}
function Pa(e) {
  return e == null ? qv : function() {
    return this.querySelector(e);
  };
}
function Jv(e) {
  typeof e != "function" && (e = Pa(e));
  for (var t = this._groups, n = t.length, r = new Array(n), o = 0; o < n; ++o)
    for (var i = t[o], s = i.length, l = r[o] = new Array(s), u, a, d = 0; d < s; ++d)
      (u = i[d]) && (a = e.call(u, u.__data__, d, i)) && ("__data__" in u && (a.__data__ = u.__data__), l[d] = a);
  return new be(r, this._parents);
}
function ew(e) {
  return e == null ? [] : Array.isArray(e) ? e : Array.from(e);
}
function tw() {
  return [];
}
function Pp(e) {
  return e == null ? tw : function() {
    return this.querySelectorAll(e);
  };
}
function nw(e) {
  return function() {
    return ew(e.apply(this, arguments));
  };
}
function rw(e) {
  typeof e == "function" ? e = nw(e) : e = Pp(e);
  for (var t = this._groups, n = t.length, r = [], o = [], i = 0; i < n; ++i)
    for (var s = t[i], l = s.length, u, a = 0; a < l; ++a)
      (u = s[a]) && (r.push(e.call(u, u.__data__, a, s)), o.push(u));
  return new be(r, o);
}
function zp(e) {
  return function() {
    return this.matches(e);
  };
}
function Ip(e) {
  return function(t) {
    return t.matches(e);
  };
}
var ow = Array.prototype.find;
function iw(e) {
  return function() {
    return ow.call(this.children, e);
  };
}
function sw() {
  return this.firstElementChild;
}
function lw(e) {
  return this.select(e == null ? sw : iw(typeof e == "function" ? e : Ip(e)));
}
var uw = Array.prototype.filter;
function aw() {
  return Array.from(this.children);
}
function cw(e) {
  return function() {
    return uw.call(this.children, e);
  };
}
function fw(e) {
  return this.selectAll(e == null ? aw : cw(typeof e == "function" ? e : Ip(e)));
}
function dw(e) {
  typeof e != "function" && (e = zp(e));
  for (var t = this._groups, n = t.length, r = new Array(n), o = 0; o < n; ++o)
    for (var i = t[o], s = i.length, l = r[o] = [], u, a = 0; a < s; ++a)
      (u = i[a]) && e.call(u, u.__data__, a, i) && l.push(u);
  return new be(r, this._parents);
}
function Tp(e) {
  return new Array(e.length);
}
function hw() {
  return new be(this._enter || this._groups.map(Tp), this._parents);
}
function ts(e, t) {
  this.ownerDocument = e.ownerDocument, this.namespaceURI = e.namespaceURI, this._next = null, this._parent = e, this.__data__ = t;
}
ts.prototype = {
  constructor: ts,
  appendChild: function(e) {
    return this._parent.insertBefore(e, this._next);
  },
  insertBefore: function(e, t) {
    return this._parent.insertBefore(e, t);
  },
  querySelector: function(e) {
    return this._parent.querySelector(e);
  },
  querySelectorAll: function(e) {
    return this._parent.querySelectorAll(e);
  }
};
function pw(e) {
  return function() {
    return e;
  };
}
function gw(e, t, n, r, o, i) {
  for (var s = 0, l, u = t.length, a = i.length; s < a; ++s)
    (l = t[s]) ? (l.__data__ = i[s], r[s] = l) : n[s] = new ts(e, i[s]);
  for (; s < u; ++s)
    (l = t[s]) && (o[s] = l);
}
function mw(e, t, n, r, o, i, s) {
  var l, u, a = /* @__PURE__ */ new Map(), d = t.length, c = i.length, f = new Array(d), m;
  for (l = 0; l < d; ++l)
    (u = t[l]) && (f[l] = m = s.call(u, u.__data__, l, t) + "", a.has(m) ? o[l] = u : a.set(m, u));
  for (l = 0; l < c; ++l)
    m = s.call(e, i[l], l, i) + "", (u = a.get(m)) ? (r[l] = u, u.__data__ = i[l], a.delete(m)) : n[l] = new ts(e, i[l]);
  for (l = 0; l < d; ++l)
    (u = t[l]) && a.get(f[l]) === u && (o[l] = u);
}
function yw(e) {
  return e.__data__;
}
function vw(e, t) {
  if (!arguments.length) return Array.from(this, yw);
  var n = t ? mw : gw, r = this._parents, o = this._groups;
  typeof e != "function" && (e = pw(e));
  for (var i = o.length, s = new Array(i), l = new Array(i), u = new Array(i), a = 0; a < i; ++a) {
    var d = r[a], c = o[a], f = c.length, m = ww(e.call(d, d && d.__data__, a, r)), y = m.length, w = l[a] = new Array(y), x = s[a] = new Array(y), p = u[a] = new Array(f);
    n(d, c, w, x, p, m, t);
    for (var g = 0, h = 0, v, S; g < y; ++g)
      if (v = w[g]) {
        for (g >= h && (h = g + 1); !(S = x[h]) && ++h < y; ) ;
        v._next = S || null;
      }
  }
  return s = new be(s, r), s._enter = l, s._exit = u, s;
}
function ww(e) {
  return typeof e == "object" && "length" in e ? e : Array.from(e);
}
function xw() {
  return new be(this._exit || this._groups.map(Tp), this._parents);
}
function Sw(e, t, n) {
  var r = this.enter(), o = this, i = this.exit();
  return typeof e == "function" ? (r = e(r), r && (r = r.selection())) : r = r.append(e + ""), t != null && (o = t(o), o && (o = o.selection())), n == null ? i.remove() : n(i), r && o ? r.merge(o).order() : o;
}
function _w(e) {
  for (var t = e.selection ? e.selection() : e, n = this._groups, r = t._groups, o = n.length, i = r.length, s = Math.min(o, i), l = new Array(o), u = 0; u < s; ++u)
    for (var a = n[u], d = r[u], c = a.length, f = l[u] = new Array(c), m, y = 0; y < c; ++y)
      (m = a[y] || d[y]) && (f[y] = m);
  for (; u < o; ++u)
    l[u] = n[u];
  return new be(l, this._parents);
}
function Ew() {
  for (var e = this._groups, t = -1, n = e.length; ++t < n; )
    for (var r = e[t], o = r.length - 1, i = r[o], s; --o >= 0; )
      (s = r[o]) && (i && s.compareDocumentPosition(i) ^ 4 && i.parentNode.insertBefore(s, i), i = s);
  return this;
}
function kw(e) {
  e || (e = Nw);
  function t(c, f) {
    return c && f ? e(c.__data__, f.__data__) : !c - !f;
  }
  for (var n = this._groups, r = n.length, o = new Array(r), i = 0; i < r; ++i) {
    for (var s = n[i], l = s.length, u = o[i] = new Array(l), a, d = 0; d < l; ++d)
      (a = s[d]) && (u[d] = a);
    u.sort(t);
  }
  return new be(o, this._parents).order();
}
function Nw(e, t) {
  return e < t ? -1 : e > t ? 1 : e >= t ? 0 : NaN;
}
function Cw() {
  var e = arguments[0];
  return arguments[0] = this, e.apply(null, arguments), this;
}
function Mw() {
  return Array.from(this);
}
function Pw() {
  for (var e = this._groups, t = 0, n = e.length; t < n; ++t)
    for (var r = e[t], o = 0, i = r.length; o < i; ++o) {
      var s = r[o];
      if (s) return s;
    }
  return null;
}
function zw() {
  let e = 0;
  for (const t of this) ++e;
  return e;
}
function Iw() {
  return !this.node();
}
function Tw(e) {
  for (var t = this._groups, n = 0, r = t.length; n < r; ++n)
    for (var o = t[n], i = 0, s = o.length, l; i < s; ++i)
      (l = o[i]) && e.call(l, l.__data__, i, o);
  return this;
}
function Lw(e) {
  return function() {
    this.removeAttribute(e);
  };
}
function Aw(e) {
  return function() {
    this.removeAttributeNS(e.space, e.local);
  };
}
function $w(e, t) {
  return function() {
    this.setAttribute(e, t);
  };
}
function Dw(e, t) {
  return function() {
    this.setAttributeNS(e.space, e.local, t);
  };
}
function Rw(e, t) {
  return function() {
    var n = t.apply(this, arguments);
    n == null ? this.removeAttribute(e) : this.setAttribute(e, n);
  };
}
function jw(e, t) {
  return function() {
    var n = t.apply(this, arguments);
    n == null ? this.removeAttributeNS(e.space, e.local) : this.setAttributeNS(e.space, e.local, n);
  };
}
function Ow(e, t) {
  var n = Ps(e);
  if (arguments.length < 2) {
    var r = this.node();
    return n.local ? r.getAttributeNS(n.space, n.local) : r.getAttribute(n);
  }
  return this.each((t == null ? n.local ? Aw : Lw : typeof t == "function" ? n.local ? jw : Rw : n.local ? Dw : $w)(n, t));
}
function Lp(e) {
  return e.ownerDocument && e.ownerDocument.defaultView || e.document && e || e.defaultView;
}
function Fw(e) {
  return function() {
    this.style.removeProperty(e);
  };
}
function Hw(e, t, n) {
  return function() {
    this.style.setProperty(e, t, n);
  };
}
function Vw(e, t, n) {
  return function() {
    var r = t.apply(this, arguments);
    r == null ? this.style.removeProperty(e) : this.style.setProperty(e, r, n);
  };
}
function Bw(e, t, n) {
  return arguments.length > 1 ? this.each((t == null ? Fw : typeof t == "function" ? Vw : Hw)(e, t, n ?? "")) : dr(this.node(), e);
}
function dr(e, t) {
  return e.style.getPropertyValue(t) || Lp(e).getComputedStyle(e, null).getPropertyValue(t);
}
function Uw(e) {
  return function() {
    delete this[e];
  };
}
function Ww(e, t) {
  return function() {
    this[e] = t;
  };
}
function Yw(e, t) {
  return function() {
    var n = t.apply(this, arguments);
    n == null ? delete this[e] : this[e] = n;
  };
}
function Xw(e, t) {
  return arguments.length > 1 ? this.each((t == null ? Uw : typeof t == "function" ? Yw : Ww)(e, t)) : this.node()[e];
}
function Ap(e) {
  return e.trim().split(/^|\s+/);
}
function za(e) {
  return e.classList || new $p(e);
}
function $p(e) {
  this._node = e, this._names = Ap(e.getAttribute("class") || "");
}
$p.prototype = {
  add: function(e) {
    var t = this._names.indexOf(e);
    t < 0 && (this._names.push(e), this._node.setAttribute("class", this._names.join(" ")));
  },
  remove: function(e) {
    var t = this._names.indexOf(e);
    t >= 0 && (this._names.splice(t, 1), this._node.setAttribute("class", this._names.join(" ")));
  },
  contains: function(e) {
    return this._names.indexOf(e) >= 0;
  }
};
function Dp(e, t) {
  for (var n = za(e), r = -1, o = t.length; ++r < o; ) n.add(t[r]);
}
function Rp(e, t) {
  for (var n = za(e), r = -1, o = t.length; ++r < o; ) n.remove(t[r]);
}
function Qw(e) {
  return function() {
    Dp(this, e);
  };
}
function bw(e) {
  return function() {
    Rp(this, e);
  };
}
function Kw(e, t) {
  return function() {
    (t.apply(this, arguments) ? Dp : Rp)(this, e);
  };
}
function Gw(e, t) {
  var n = Ap(e + "");
  if (arguments.length < 2) {
    for (var r = za(this.node()), o = -1, i = n.length; ++o < i; ) if (!r.contains(n[o])) return !1;
    return !0;
  }
  return this.each((typeof t == "function" ? Kw : t ? Qw : bw)(n, t));
}
function Zw() {
  this.textContent = "";
}
function qw(e) {
  return function() {
    this.textContent = e;
  };
}
function Jw(e) {
  return function() {
    var t = e.apply(this, arguments);
    this.textContent = t ?? "";
  };
}
function ex(e) {
  return arguments.length ? this.each(e == null ? Zw : (typeof e == "function" ? Jw : qw)(e)) : this.node().textContent;
}
function tx() {
  this.innerHTML = "";
}
function nx(e) {
  return function() {
    this.innerHTML = e;
  };
}
function rx(e) {
  return function() {
    var t = e.apply(this, arguments);
    this.innerHTML = t ?? "";
  };
}
function ox(e) {
  return arguments.length ? this.each(e == null ? tx : (typeof e == "function" ? rx : nx)(e)) : this.node().innerHTML;
}
function ix() {
  this.nextSibling && this.parentNode.appendChild(this);
}
function sx() {
  return this.each(ix);
}
function lx() {
  this.previousSibling && this.parentNode.insertBefore(this, this.parentNode.firstChild);
}
function ux() {
  return this.each(lx);
}
function ax(e) {
  var t = typeof e == "function" ? e : Mp(e);
  return this.select(function() {
    return this.appendChild(t.apply(this, arguments));
  });
}
function cx() {
  return null;
}
function fx(e, t) {
  var n = typeof e == "function" ? e : Mp(e), r = t == null ? cx : typeof t == "function" ? t : Pa(t);
  return this.select(function() {
    return this.insertBefore(n.apply(this, arguments), r.apply(this, arguments) || null);
  });
}
function dx() {
  var e = this.parentNode;
  e && e.removeChild(this);
}
function hx() {
  return this.each(dx);
}
function px() {
  var e = this.cloneNode(!1), t = this.parentNode;
  return t ? t.insertBefore(e, this.nextSibling) : e;
}
function gx() {
  var e = this.cloneNode(!0), t = this.parentNode;
  return t ? t.insertBefore(e, this.nextSibling) : e;
}
function mx(e) {
  return this.select(e ? gx : px);
}
function yx(e) {
  return arguments.length ? this.property("__data__", e) : this.node().__data__;
}
function vx(e) {
  return function(t) {
    e.call(this, t, this.__data__);
  };
}
function wx(e) {
  return e.trim().split(/^|\s+/).map(function(t) {
    var n = "", r = t.indexOf(".");
    return r >= 0 && (n = t.slice(r + 1), t = t.slice(0, r)), { type: t, name: n };
  });
}
function xx(e) {
  return function() {
    var t = this.__on;
    if (t) {
      for (var n = 0, r = -1, o = t.length, i; n < o; ++n)
        i = t[n], (!e.type || i.type === e.type) && i.name === e.name ? this.removeEventListener(i.type, i.listener, i.options) : t[++r] = i;
      ++r ? t.length = r : delete this.__on;
    }
  };
}
function Sx(e, t, n) {
  return function() {
    var r = this.__on, o, i = vx(t);
    if (r) {
      for (var s = 0, l = r.length; s < l; ++s)
        if ((o = r[s]).type === e.type && o.name === e.name) {
          this.removeEventListener(o.type, o.listener, o.options), this.addEventListener(o.type, o.listener = i, o.options = n), o.value = t;
          return;
        }
    }
    this.addEventListener(e.type, i, n), o = { type: e.type, name: e.name, value: t, listener: i, options: n }, r ? r.push(o) : this.__on = [o];
  };
}
function _x(e, t, n) {
  var r = wx(e + ""), o, i = r.length, s;
  if (arguments.length < 2) {
    var l = this.node().__on;
    if (l) {
      for (var u = 0, a = l.length, d; u < a; ++u)
        for (o = 0, d = l[u]; o < i; ++o)
          if ((s = r[o]).type === d.type && s.name === d.name)
            return d.value;
    }
    return;
  }
  for (l = t ? Sx : xx, o = 0; o < i; ++o) this.each(l(r[o], t, n));
  return this;
}
function jp(e, t, n) {
  var r = Lp(e), o = r.CustomEvent;
  typeof o == "function" ? o = new o(t, n) : (o = r.document.createEvent("Event"), n ? (o.initEvent(t, n.bubbles, n.cancelable), o.detail = n.detail) : o.initEvent(t, !1, !1)), e.dispatchEvent(o);
}
function Ex(e, t) {
  return function() {
    return jp(this, e, t);
  };
}
function kx(e, t) {
  return function() {
    return jp(this, e, t.apply(this, arguments));
  };
}
function Nx(e, t) {
  return this.each((typeof t == "function" ? kx : Ex)(e, t));
}
function* Cx() {
  for (var e = this._groups, t = 0, n = e.length; t < n; ++t)
    for (var r = e[t], o = 0, i = r.length, s; o < i; ++o)
      (s = r[o]) && (yield s);
}
var Op = [null];
function be(e, t) {
  this._groups = e, this._parents = t;
}
function $o() {
  return new be([[document.documentElement]], Op);
}
function Mx() {
  return this;
}
be.prototype = $o.prototype = {
  constructor: be,
  select: Jv,
  selectAll: rw,
  selectChild: lw,
  selectChildren: fw,
  filter: dw,
  data: vw,
  enter: hw,
  exit: xw,
  join: Sw,
  merge: _w,
  selection: Mx,
  order: Ew,
  sort: kw,
  call: Cw,
  nodes: Mw,
  node: Pw,
  size: zw,
  empty: Iw,
  each: Tw,
  attr: Ow,
  style: Bw,
  property: Xw,
  classed: Gw,
  text: ex,
  html: ox,
  raise: sx,
  lower: ux,
  append: ax,
  insert: fx,
  remove: hx,
  clone: mx,
  datum: yx,
  on: _x,
  dispatch: Nx,
  [Symbol.iterator]: Cx
};
function We(e) {
  return typeof e == "string" ? new be([[document.querySelector(e)]], [document.documentElement]) : new be([[e]], Op);
}
function Px(e) {
  let t;
  for (; t = e.sourceEvent; ) e = t;
  return e;
}
function st(e, t) {
  if (e = Px(e), t === void 0 && (t = e.currentTarget), t) {
    var n = t.ownerSVGElement || t;
    if (n.createSVGPoint) {
      var r = n.createSVGPoint();
      return r.x = e.clientX, r.y = e.clientY, r = r.matrixTransform(t.getScreenCTM().inverse()), [r.x, r.y];
    }
    if (t.getBoundingClientRect) {
      var o = t.getBoundingClientRect();
      return [e.clientX - o.left - t.clientLeft, e.clientY - o.top - t.clientTop];
    }
  }
  return [e.pageX, e.pageY];
}
const zx = { passive: !1 }, wo = { capture: !0, passive: !1 };
function ml(e) {
  e.stopImmediatePropagation();
}
function rr(e) {
  e.preventDefault(), e.stopImmediatePropagation();
}
function Fp(e) {
  var t = e.document.documentElement, n = We(e).on("dragstart.drag", rr, wo);
  "onselectstart" in t ? n.on("selectstart.drag", rr, wo) : (t.__noselect = t.style.MozUserSelect, t.style.MozUserSelect = "none");
}
function Hp(e, t) {
  var n = e.document.documentElement, r = We(e).on("dragstart.drag", null);
  t && (r.on("click.drag", rr, wo), setTimeout(function() {
    r.on("click.drag", null);
  }, 0)), "onselectstart" in n ? r.on("selectstart.drag", null) : (n.style.MozUserSelect = n.__noselect, delete n.__noselect);
}
const ii = (e) => () => e;
function wu(e, {
  sourceEvent: t,
  subject: n,
  target: r,
  identifier: o,
  active: i,
  x: s,
  y: l,
  dx: u,
  dy: a,
  dispatch: d
}) {
  Object.defineProperties(this, {
    type: { value: e, enumerable: !0, configurable: !0 },
    sourceEvent: { value: t, enumerable: !0, configurable: !0 },
    subject: { value: n, enumerable: !0, configurable: !0 },
    target: { value: r, enumerable: !0, configurable: !0 },
    identifier: { value: o, enumerable: !0, configurable: !0 },
    active: { value: i, enumerable: !0, configurable: !0 },
    x: { value: s, enumerable: !0, configurable: !0 },
    y: { value: l, enumerable: !0, configurable: !0 },
    dx: { value: u, enumerable: !0, configurable: !0 },
    dy: { value: a, enumerable: !0, configurable: !0 },
    _: { value: d }
  });
}
wu.prototype.on = function() {
  var e = this._.on.apply(this._, arguments);
  return e === this._ ? this : e;
};
function Ix(e) {
  return !e.ctrlKey && !e.button;
}
function Tx() {
  return this.parentNode;
}
function Lx(e, t) {
  return t ?? { x: e.x, y: e.y };
}
function Ax() {
  return navigator.maxTouchPoints || "ontouchstart" in this;
}
function Vp() {
  var e = Ix, t = Tx, n = Lx, r = Ax, o = {}, i = Ms("start", "drag", "end"), s = 0, l, u, a, d, c = 0;
  function f(v) {
    v.on("mousedown.drag", m).filter(r).on("touchstart.drag", x).on("touchmove.drag", p, zx).on("touchend.drag touchcancel.drag", g).style("touch-action", "none").style("-webkit-tap-highlight-color", "rgba(0,0,0,0)");
  }
  function m(v, S) {
    if (!(d || !e.call(this, v, S))) {
      var E = h(this, t.call(this, v, S), v, S, "mouse");
      E && (We(v.view).on("mousemove.drag", y, wo).on("mouseup.drag", w, wo), Fp(v.view), ml(v), a = !1, l = v.clientX, u = v.clientY, E("start", v));
    }
  }
  function y(v) {
    if (rr(v), !a) {
      var S = v.clientX - l, E = v.clientY - u;
      a = S * S + E * E > c;
    }
    o.mouse("drag", v);
  }
  function w(v) {
    We(v.view).on("mousemove.drag mouseup.drag", null), Hp(v.view, a), rr(v), o.mouse("end", v);
  }
  function x(v, S) {
    if (e.call(this, v, S)) {
      var E = v.changedTouches, M = t.call(this, v, S), P = E.length, T, j;
      for (T = 0; T < P; ++T)
        (j = h(this, M, v, S, E[T].identifier, E[T])) && (ml(v), j("start", v, E[T]));
    }
  }
  function p(v) {
    var S = v.changedTouches, E = S.length, M, P;
    for (M = 0; M < E; ++M)
      (P = o[S[M].identifier]) && (rr(v), P("drag", v, S[M]));
  }
  function g(v) {
    var S = v.changedTouches, E = S.length, M, P;
    for (d && clearTimeout(d), d = setTimeout(function() {
      d = null;
    }, 500), M = 0; M < E; ++M)
      (P = o[S[M].identifier]) && (ml(v), P("end", v, S[M]));
  }
  function h(v, S, E, M, P, T) {
    var j = i.copy(), I = st(T || E, S), D, H, k;
    if ((k = n.call(v, new wu("beforestart", {
      sourceEvent: E,
      target: f,
      identifier: P,
      active: s,
      x: I[0],
      y: I[1],
      dx: 0,
      dy: 0,
      dispatch: j
    }), M)) != null)
      return D = k.x - I[0] || 0, H = k.y - I[1] || 0, function A(z, $, N) {
        var C = I, L;
        switch (z) {
          case "start":
            o[P] = A, L = s++;
            break;
          case "end":
            delete o[P], --s;
          case "drag":
            I = st(N || $, S), L = s;
            break;
        }
        j.call(
          z,
          v,
          new wu(z, {
            sourceEvent: $,
            subject: k,
            target: f,
            identifier: P,
            active: L,
            x: I[0] + D,
            y: I[1] + H,
            dx: I[0] - C[0],
            dy: I[1] - C[1],
            dispatch: j
          }),
          M
        );
      };
  }
  return f.filter = function(v) {
    return arguments.length ? (e = typeof v == "function" ? v : ii(!!v), f) : e;
  }, f.container = function(v) {
    return arguments.length ? (t = typeof v == "function" ? v : ii(v), f) : t;
  }, f.subject = function(v) {
    return arguments.length ? (n = typeof v == "function" ? v : ii(v), f) : n;
  }, f.touchable = function(v) {
    return arguments.length ? (r = typeof v == "function" ? v : ii(!!v), f) : r;
  }, f.on = function() {
    var v = i.on.apply(i, arguments);
    return v === i ? f : v;
  }, f.clickDistance = function(v) {
    return arguments.length ? (c = (v = +v) * v, f) : Math.sqrt(c);
  }, f;
}
function Ia(e, t, n) {
  e.prototype = t.prototype = n, n.constructor = e;
}
function Bp(e, t) {
  var n = Object.create(e.prototype);
  for (var r in t) n[r] = t[r];
  return n;
}
function Do() {
}
var xo = 0.7, ns = 1 / xo, or = "\\s*([+-]?\\d+)\\s*", So = "\\s*([+-]?(?:\\d*\\.)?\\d+(?:[eE][+-]?\\d+)?)\\s*", St = "\\s*([+-]?(?:\\d*\\.)?\\d+(?:[eE][+-]?\\d+)?)%\\s*", $x = /^#([0-9a-f]{3,8})$/, Dx = new RegExp(`^rgb\\(${or},${or},${or}\\)$`), Rx = new RegExp(`^rgb\\(${St},${St},${St}\\)$`), jx = new RegExp(`^rgba\\(${or},${or},${or},${So}\\)$`), Ox = new RegExp(`^rgba\\(${St},${St},${St},${So}\\)$`), Fx = new RegExp(`^hsl\\(${So},${St},${St}\\)$`), Hx = new RegExp(`^hsla\\(${So},${St},${St},${So}\\)$`), df = {
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
Ia(Do, Pn, {
  copy(e) {
    return Object.assign(new this.constructor(), this, e);
  },
  displayable() {
    return this.rgb().displayable();
  },
  hex: hf,
  // Deprecated! Use color.formatHex.
  formatHex: hf,
  formatHex8: Vx,
  formatHsl: Bx,
  formatRgb: pf,
  toString: pf
});
function hf() {
  return this.rgb().formatHex();
}
function Vx() {
  return this.rgb().formatHex8();
}
function Bx() {
  return Up(this).formatHsl();
}
function pf() {
  return this.rgb().formatRgb();
}
function Pn(e) {
  var t, n;
  return e = (e + "").trim().toLowerCase(), (t = $x.exec(e)) ? (n = t[1].length, t = parseInt(t[1], 16), n === 6 ? gf(t) : n === 3 ? new Fe(t >> 8 & 15 | t >> 4 & 240, t >> 4 & 15 | t & 240, (t & 15) << 4 | t & 15, 1) : n === 8 ? si(t >> 24 & 255, t >> 16 & 255, t >> 8 & 255, (t & 255) / 255) : n === 4 ? si(t >> 12 & 15 | t >> 8 & 240, t >> 8 & 15 | t >> 4 & 240, t >> 4 & 15 | t & 240, ((t & 15) << 4 | t & 15) / 255) : null) : (t = Dx.exec(e)) ? new Fe(t[1], t[2], t[3], 1) : (t = Rx.exec(e)) ? new Fe(t[1] * 255 / 100, t[2] * 255 / 100, t[3] * 255 / 100, 1) : (t = jx.exec(e)) ? si(t[1], t[2], t[3], t[4]) : (t = Ox.exec(e)) ? si(t[1] * 255 / 100, t[2] * 255 / 100, t[3] * 255 / 100, t[4]) : (t = Fx.exec(e)) ? vf(t[1], t[2] / 100, t[3] / 100, 1) : (t = Hx.exec(e)) ? vf(t[1], t[2] / 100, t[3] / 100, t[4]) : df.hasOwnProperty(e) ? gf(df[e]) : e === "transparent" ? new Fe(NaN, NaN, NaN, 0) : null;
}
function gf(e) {
  return new Fe(e >> 16 & 255, e >> 8 & 255, e & 255, 1);
}
function si(e, t, n, r) {
  return r <= 0 && (e = t = n = NaN), new Fe(e, t, n, r);
}
function Ux(e) {
  return e instanceof Do || (e = Pn(e)), e ? (e = e.rgb(), new Fe(e.r, e.g, e.b, e.opacity)) : new Fe();
}
function xu(e, t, n, r) {
  return arguments.length === 1 ? Ux(e) : new Fe(e, t, n, r ?? 1);
}
function Fe(e, t, n, r) {
  this.r = +e, this.g = +t, this.b = +n, this.opacity = +r;
}
Ia(Fe, xu, Bp(Do, {
  brighter(e) {
    return e = e == null ? ns : Math.pow(ns, e), new Fe(this.r * e, this.g * e, this.b * e, this.opacity);
  },
  darker(e) {
    return e = e == null ? xo : Math.pow(xo, e), new Fe(this.r * e, this.g * e, this.b * e, this.opacity);
  },
  rgb() {
    return this;
  },
  clamp() {
    return new Fe(Sn(this.r), Sn(this.g), Sn(this.b), rs(this.opacity));
  },
  displayable() {
    return -0.5 <= this.r && this.r < 255.5 && -0.5 <= this.g && this.g < 255.5 && -0.5 <= this.b && this.b < 255.5 && 0 <= this.opacity && this.opacity <= 1;
  },
  hex: mf,
  // Deprecated! Use color.formatHex.
  formatHex: mf,
  formatHex8: Wx,
  formatRgb: yf,
  toString: yf
}));
function mf() {
  return `#${vn(this.r)}${vn(this.g)}${vn(this.b)}`;
}
function Wx() {
  return `#${vn(this.r)}${vn(this.g)}${vn(this.b)}${vn((isNaN(this.opacity) ? 1 : this.opacity) * 255)}`;
}
function yf() {
  const e = rs(this.opacity);
  return `${e === 1 ? "rgb(" : "rgba("}${Sn(this.r)}, ${Sn(this.g)}, ${Sn(this.b)}${e === 1 ? ")" : `, ${e})`}`;
}
function rs(e) {
  return isNaN(e) ? 1 : Math.max(0, Math.min(1, e));
}
function Sn(e) {
  return Math.max(0, Math.min(255, Math.round(e) || 0));
}
function vn(e) {
  return e = Sn(e), (e < 16 ? "0" : "") + e.toString(16);
}
function vf(e, t, n, r) {
  return r <= 0 ? e = t = n = NaN : n <= 0 || n >= 1 ? e = t = NaN : t <= 0 && (e = NaN), new ut(e, t, n, r);
}
function Up(e) {
  if (e instanceof ut) return new ut(e.h, e.s, e.l, e.opacity);
  if (e instanceof Do || (e = Pn(e)), !e) return new ut();
  if (e instanceof ut) return e;
  e = e.rgb();
  var t = e.r / 255, n = e.g / 255, r = e.b / 255, o = Math.min(t, n, r), i = Math.max(t, n, r), s = NaN, l = i - o, u = (i + o) / 2;
  return l ? (t === i ? s = (n - r) / l + (n < r) * 6 : n === i ? s = (r - t) / l + 2 : s = (t - n) / l + 4, l /= u < 0.5 ? i + o : 2 - i - o, s *= 60) : l = u > 0 && u < 1 ? 0 : s, new ut(s, l, u, e.opacity);
}
function Yx(e, t, n, r) {
  return arguments.length === 1 ? Up(e) : new ut(e, t, n, r ?? 1);
}
function ut(e, t, n, r) {
  this.h = +e, this.s = +t, this.l = +n, this.opacity = +r;
}
Ia(ut, Yx, Bp(Do, {
  brighter(e) {
    return e = e == null ? ns : Math.pow(ns, e), new ut(this.h, this.s, this.l * e, this.opacity);
  },
  darker(e) {
    return e = e == null ? xo : Math.pow(xo, e), new ut(this.h, this.s, this.l * e, this.opacity);
  },
  rgb() {
    var e = this.h % 360 + (this.h < 0) * 360, t = isNaN(e) || isNaN(this.s) ? 0 : this.s, n = this.l, r = n + (n < 0.5 ? n : 1 - n) * t, o = 2 * n - r;
    return new Fe(
      yl(e >= 240 ? e - 240 : e + 120, o, r),
      yl(e, o, r),
      yl(e < 120 ? e + 240 : e - 120, o, r),
      this.opacity
    );
  },
  clamp() {
    return new ut(wf(this.h), li(this.s), li(this.l), rs(this.opacity));
  },
  displayable() {
    return (0 <= this.s && this.s <= 1 || isNaN(this.s)) && 0 <= this.l && this.l <= 1 && 0 <= this.opacity && this.opacity <= 1;
  },
  formatHsl() {
    const e = rs(this.opacity);
    return `${e === 1 ? "hsl(" : "hsla("}${wf(this.h)}, ${li(this.s) * 100}%, ${li(this.l) * 100}%${e === 1 ? ")" : `, ${e})`}`;
  }
}));
function wf(e) {
  return e = (e || 0) % 360, e < 0 ? e + 360 : e;
}
function li(e) {
  return Math.max(0, Math.min(1, e || 0));
}
function yl(e, t, n) {
  return (e < 60 ? t + (n - t) * e / 60 : e < 180 ? n : e < 240 ? t + (n - t) * (240 - e) / 60 : t) * 255;
}
const Ta = (e) => () => e;
function Xx(e, t) {
  return function(n) {
    return e + n * t;
  };
}
function Qx(e, t, n) {
  return e = Math.pow(e, n), t = Math.pow(t, n) - e, n = 1 / n, function(r) {
    return Math.pow(e + r * t, n);
  };
}
function bx(e) {
  return (e = +e) == 1 ? Wp : function(t, n) {
    return n - t ? Qx(t, n, e) : Ta(isNaN(t) ? n : t);
  };
}
function Wp(e, t) {
  var n = t - e;
  return n ? Xx(e, n) : Ta(isNaN(e) ? t : e);
}
const os = function e(t) {
  var n = bx(t);
  function r(o, i) {
    var s = n((o = xu(o)).r, (i = xu(i)).r), l = n(o.g, i.g), u = n(o.b, i.b), a = Wp(o.opacity, i.opacity);
    return function(d) {
      return o.r = s(d), o.g = l(d), o.b = u(d), o.opacity = a(d), o + "";
    };
  }
  return r.gamma = e, r;
}(1);
function Kx(e, t) {
  t || (t = []);
  var n = e ? Math.min(t.length, e.length) : 0, r = t.slice(), o;
  return function(i) {
    for (o = 0; o < n; ++o) r[o] = e[o] * (1 - i) + t[o] * i;
    return r;
  };
}
function Gx(e) {
  return ArrayBuffer.isView(e) && !(e instanceof DataView);
}
function Zx(e, t) {
  var n = t ? t.length : 0, r = e ? Math.min(n, e.length) : 0, o = new Array(r), i = new Array(n), s;
  for (s = 0; s < r; ++s) o[s] = Jr(e[s], t[s]);
  for (; s < n; ++s) i[s] = t[s];
  return function(l) {
    for (s = 0; s < r; ++s) i[s] = o[s](l);
    return i;
  };
}
function qx(e, t) {
  var n = /* @__PURE__ */ new Date();
  return e = +e, t = +t, function(r) {
    return n.setTime(e * (1 - r) + t * r), n;
  };
}
function yt(e, t) {
  return e = +e, t = +t, function(n) {
    return e * (1 - n) + t * n;
  };
}
function Jx(e, t) {
  var n = {}, r = {}, o;
  (e === null || typeof e != "object") && (e = {}), (t === null || typeof t != "object") && (t = {});
  for (o in t)
    o in e ? n[o] = Jr(e[o], t[o]) : r[o] = t[o];
  return function(i) {
    for (o in n) r[o] = n[o](i);
    return r;
  };
}
var Su = /[-+]?(?:\d+\.?\d*|\.?\d+)(?:[eE][-+]?\d+)?/g, vl = new RegExp(Su.source, "g");
function e1(e) {
  return function() {
    return e;
  };
}
function t1(e) {
  return function(t) {
    return e(t) + "";
  };
}
function Yp(e, t) {
  var n = Su.lastIndex = vl.lastIndex = 0, r, o, i, s = -1, l = [], u = [];
  for (e = e + "", t = t + ""; (r = Su.exec(e)) && (o = vl.exec(t)); )
    (i = o.index) > n && (i = t.slice(n, i), l[s] ? l[s] += i : l[++s] = i), (r = r[0]) === (o = o[0]) ? l[s] ? l[s] += o : l[++s] = o : (l[++s] = null, u.push({ i: s, x: yt(r, o) })), n = vl.lastIndex;
  return n < t.length && (i = t.slice(n), l[s] ? l[s] += i : l[++s] = i), l.length < 2 ? u[0] ? t1(u[0].x) : e1(t) : (t = u.length, function(a) {
    for (var d = 0, c; d < t; ++d) l[(c = u[d]).i] = c.x(a);
    return l.join("");
  });
}
function Jr(e, t) {
  var n = typeof t, r;
  return t == null || n === "boolean" ? Ta(t) : (n === "number" ? yt : n === "string" ? (r = Pn(t)) ? (t = r, os) : Yp : t instanceof Pn ? os : t instanceof Date ? qx : Gx(t) ? Kx : Array.isArray(t) ? Zx : typeof t.valueOf != "function" && typeof t.toString != "function" || isNaN(t) ? Jx : yt)(e, t);
}
var xf = 180 / Math.PI, _u = {
  translateX: 0,
  translateY: 0,
  rotate: 0,
  skewX: 0,
  scaleX: 1,
  scaleY: 1
};
function Xp(e, t, n, r, o, i) {
  var s, l, u;
  return (s = Math.sqrt(e * e + t * t)) && (e /= s, t /= s), (u = e * n + t * r) && (n -= e * u, r -= t * u), (l = Math.sqrt(n * n + r * r)) && (n /= l, r /= l, u /= l), e * r < t * n && (e = -e, t = -t, u = -u, s = -s), {
    translateX: o,
    translateY: i,
    rotate: Math.atan2(t, e) * xf,
    skewX: Math.atan(u) * xf,
    scaleX: s,
    scaleY: l
  };
}
var ui;
function n1(e) {
  const t = new (typeof DOMMatrix == "function" ? DOMMatrix : WebKitCSSMatrix)(e + "");
  return t.isIdentity ? _u : Xp(t.a, t.b, t.c, t.d, t.e, t.f);
}
function r1(e) {
  return e == null || (ui || (ui = document.createElementNS("http://www.w3.org/2000/svg", "g")), ui.setAttribute("transform", e), !(e = ui.transform.baseVal.consolidate())) ? _u : (e = e.matrix, Xp(e.a, e.b, e.c, e.d, e.e, e.f));
}
function Qp(e, t, n, r) {
  function o(a) {
    return a.length ? a.pop() + " " : "";
  }
  function i(a, d, c, f, m, y) {
    if (a !== c || d !== f) {
      var w = m.push("translate(", null, t, null, n);
      y.push({ i: w - 4, x: yt(a, c) }, { i: w - 2, x: yt(d, f) });
    } else (c || f) && m.push("translate(" + c + t + f + n);
  }
  function s(a, d, c, f) {
    a !== d ? (a - d > 180 ? d += 360 : d - a > 180 && (a += 360), f.push({ i: c.push(o(c) + "rotate(", null, r) - 2, x: yt(a, d) })) : d && c.push(o(c) + "rotate(" + d + r);
  }
  function l(a, d, c, f) {
    a !== d ? f.push({ i: c.push(o(c) + "skewX(", null, r) - 2, x: yt(a, d) }) : d && c.push(o(c) + "skewX(" + d + r);
  }
  function u(a, d, c, f, m, y) {
    if (a !== c || d !== f) {
      var w = m.push(o(m) + "scale(", null, ",", null, ")");
      y.push({ i: w - 4, x: yt(a, c) }, { i: w - 2, x: yt(d, f) });
    } else (c !== 1 || f !== 1) && m.push(o(m) + "scale(" + c + "," + f + ")");
  }
  return function(a, d) {
    var c = [], f = [];
    return a = e(a), d = e(d), i(a.translateX, a.translateY, d.translateX, d.translateY, c, f), s(a.rotate, d.rotate, c, f), l(a.skewX, d.skewX, c, f), u(a.scaleX, a.scaleY, d.scaleX, d.scaleY, c, f), a = d = null, function(m) {
      for (var y = -1, w = f.length, x; ++y < w; ) c[(x = f[y]).i] = x.x(m);
      return c.join("");
    };
  };
}
var o1 = Qp(n1, "px, ", "px)", "deg)"), i1 = Qp(r1, ", ", ")", ")"), s1 = 1e-12;
function Sf(e) {
  return ((e = Math.exp(e)) + 1 / e) / 2;
}
function l1(e) {
  return ((e = Math.exp(e)) - 1 / e) / 2;
}
function u1(e) {
  return ((e = Math.exp(2 * e)) - 1) / (e + 1);
}
const Mi = function e(t, n, r) {
  function o(i, s) {
    var l = i[0], u = i[1], a = i[2], d = s[0], c = s[1], f = s[2], m = d - l, y = c - u, w = m * m + y * y, x, p;
    if (w < s1)
      p = Math.log(f / a) / t, x = function(M) {
        return [
          l + M * m,
          u + M * y,
          a * Math.exp(t * M * p)
        ];
      };
    else {
      var g = Math.sqrt(w), h = (f * f - a * a + r * w) / (2 * a * n * g), v = (f * f - a * a - r * w) / (2 * f * n * g), S = Math.log(Math.sqrt(h * h + 1) - h), E = Math.log(Math.sqrt(v * v + 1) - v);
      p = (E - S) / t, x = function(M) {
        var P = M * p, T = Sf(S), j = a / (n * g) * (T * u1(t * P + S) - l1(S));
        return [
          l + j * m,
          u + j * y,
          a * T / Sf(t * P + S)
        ];
      };
    }
    return x.duration = p * 1e3 * t / Math.SQRT2, x;
  }
  return o.rho = function(i) {
    var s = Math.max(1e-3, +i), l = s * s, u = l * l;
    return e(s, l, u);
  }, o;
}(Math.SQRT2, 2, 4);
var hr = 0, Br = 0, Rr = 0, bp = 1e3, is, Ur, ss = 0, zn = 0, zs = 0, _o = typeof performance == "object" && performance.now ? performance : Date, Kp = typeof window == "object" && window.requestAnimationFrame ? window.requestAnimationFrame.bind(window) : function(e) {
  setTimeout(e, 17);
};
function La() {
  return zn || (Kp(a1), zn = _o.now() + zs);
}
function a1() {
  zn = 0;
}
function ls() {
  this._call = this._time = this._next = null;
}
ls.prototype = Gp.prototype = {
  constructor: ls,
  restart: function(e, t, n) {
    if (typeof e != "function") throw new TypeError("callback is not a function");
    n = (n == null ? La() : +n) + (t == null ? 0 : +t), !this._next && Ur !== this && (Ur ? Ur._next = this : is = this, Ur = this), this._call = e, this._time = n, Eu();
  },
  stop: function() {
    this._call && (this._call = null, this._time = 1 / 0, Eu());
  }
};
function Gp(e, t, n) {
  var r = new ls();
  return r.restart(e, t, n), r;
}
function c1() {
  La(), ++hr;
  for (var e = is, t; e; )
    (t = zn - e._time) >= 0 && e._call.call(void 0, t), e = e._next;
  --hr;
}
function _f() {
  zn = (ss = _o.now()) + zs, hr = Br = 0;
  try {
    c1();
  } finally {
    hr = 0, d1(), zn = 0;
  }
}
function f1() {
  var e = _o.now(), t = e - ss;
  t > bp && (zs -= t, ss = e);
}
function d1() {
  for (var e, t = is, n, r = 1 / 0; t; )
    t._call ? (r > t._time && (r = t._time), e = t, t = t._next) : (n = t._next, t._next = null, t = e ? e._next = n : is = n);
  Ur = e, Eu(r);
}
function Eu(e) {
  if (!hr) {
    Br && (Br = clearTimeout(Br));
    var t = e - zn;
    t > 24 ? (e < 1 / 0 && (Br = setTimeout(_f, e - _o.now() - zs)), Rr && (Rr = clearInterval(Rr))) : (Rr || (ss = _o.now(), Rr = setInterval(f1, bp)), hr = 1, Kp(_f));
  }
}
function Ef(e, t, n) {
  var r = new ls();
  return t = t == null ? 0 : +t, r.restart((o) => {
    r.stop(), e(o + t);
  }, t, n), r;
}
var h1 = Ms("start", "end", "cancel", "interrupt"), p1 = [], Zp = 0, kf = 1, ku = 2, Pi = 3, Nf = 4, Nu = 5, zi = 6;
function Is(e, t, n, r, o, i) {
  var s = e.__transition;
  if (!s) e.__transition = {};
  else if (n in s) return;
  g1(e, n, {
    name: t,
    index: r,
    // For context during callback.
    group: o,
    // For context during callback.
    on: h1,
    tween: p1,
    time: i.time,
    delay: i.delay,
    duration: i.duration,
    ease: i.ease,
    timer: null,
    state: Zp
  });
}
function Aa(e, t) {
  var n = pt(e, t);
  if (n.state > Zp) throw new Error("too late; already scheduled");
  return n;
}
function Et(e, t) {
  var n = pt(e, t);
  if (n.state > Pi) throw new Error("too late; already running");
  return n;
}
function pt(e, t) {
  var n = e.__transition;
  if (!n || !(n = n[t])) throw new Error("transition not found");
  return n;
}
function g1(e, t, n) {
  var r = e.__transition, o;
  r[t] = n, n.timer = Gp(i, 0, n.time);
  function i(a) {
    n.state = kf, n.timer.restart(s, n.delay, n.time), n.delay <= a && s(a - n.delay);
  }
  function s(a) {
    var d, c, f, m;
    if (n.state !== kf) return u();
    for (d in r)
      if (m = r[d], m.name === n.name) {
        if (m.state === Pi) return Ef(s);
        m.state === Nf ? (m.state = zi, m.timer.stop(), m.on.call("interrupt", e, e.__data__, m.index, m.group), delete r[d]) : +d < t && (m.state = zi, m.timer.stop(), m.on.call("cancel", e, e.__data__, m.index, m.group), delete r[d]);
      }
    if (Ef(function() {
      n.state === Pi && (n.state = Nf, n.timer.restart(l, n.delay, n.time), l(a));
    }), n.state = ku, n.on.call("start", e, e.__data__, n.index, n.group), n.state === ku) {
      for (n.state = Pi, o = new Array(f = n.tween.length), d = 0, c = -1; d < f; ++d)
        (m = n.tween[d].value.call(e, e.__data__, n.index, n.group)) && (o[++c] = m);
      o.length = c + 1;
    }
  }
  function l(a) {
    for (var d = a < n.duration ? n.ease.call(null, a / n.duration) : (n.timer.restart(u), n.state = Nu, 1), c = -1, f = o.length; ++c < f; )
      o[c].call(e, d);
    n.state === Nu && (n.on.call("end", e, e.__data__, n.index, n.group), u());
  }
  function u() {
    n.state = zi, n.timer.stop(), delete r[t];
    for (var a in r) return;
    delete e.__transition;
  }
}
function Ii(e, t) {
  var n = e.__transition, r, o, i = !0, s;
  if (n) {
    t = t == null ? null : t + "";
    for (s in n) {
      if ((r = n[s]).name !== t) {
        i = !1;
        continue;
      }
      o = r.state > ku && r.state < Nu, r.state = zi, r.timer.stop(), r.on.call(o ? "interrupt" : "cancel", e, e.__data__, r.index, r.group), delete n[s];
    }
    i && delete e.__transition;
  }
}
function m1(e) {
  return this.each(function() {
    Ii(this, e);
  });
}
function y1(e, t) {
  var n, r;
  return function() {
    var o = Et(this, e), i = o.tween;
    if (i !== n) {
      r = n = i;
      for (var s = 0, l = r.length; s < l; ++s)
        if (r[s].name === t) {
          r = r.slice(), r.splice(s, 1);
          break;
        }
    }
    o.tween = r;
  };
}
function v1(e, t, n) {
  var r, o;
  if (typeof n != "function") throw new Error();
  return function() {
    var i = Et(this, e), s = i.tween;
    if (s !== r) {
      o = (r = s).slice();
      for (var l = { name: t, value: n }, u = 0, a = o.length; u < a; ++u)
        if (o[u].name === t) {
          o[u] = l;
          break;
        }
      u === a && o.push(l);
    }
    i.tween = o;
  };
}
function w1(e, t) {
  var n = this._id;
  if (e += "", arguments.length < 2) {
    for (var r = pt(this.node(), n).tween, o = 0, i = r.length, s; o < i; ++o)
      if ((s = r[o]).name === e)
        return s.value;
    return null;
  }
  return this.each((t == null ? y1 : v1)(n, e, t));
}
function $a(e, t, n) {
  var r = e._id;
  return e.each(function() {
    var o = Et(this, r);
    (o.value || (o.value = {}))[t] = n.apply(this, arguments);
  }), function(o) {
    return pt(o, r).value[t];
  };
}
function qp(e, t) {
  var n;
  return (typeof t == "number" ? yt : t instanceof Pn ? os : (n = Pn(t)) ? (t = n, os) : Yp)(e, t);
}
function x1(e) {
  return function() {
    this.removeAttribute(e);
  };
}
function S1(e) {
  return function() {
    this.removeAttributeNS(e.space, e.local);
  };
}
function _1(e, t, n) {
  var r, o = n + "", i;
  return function() {
    var s = this.getAttribute(e);
    return s === o ? null : s === r ? i : i = t(r = s, n);
  };
}
function E1(e, t, n) {
  var r, o = n + "", i;
  return function() {
    var s = this.getAttributeNS(e.space, e.local);
    return s === o ? null : s === r ? i : i = t(r = s, n);
  };
}
function k1(e, t, n) {
  var r, o, i;
  return function() {
    var s, l = n(this), u;
    return l == null ? void this.removeAttribute(e) : (s = this.getAttribute(e), u = l + "", s === u ? null : s === r && u === o ? i : (o = u, i = t(r = s, l)));
  };
}
function N1(e, t, n) {
  var r, o, i;
  return function() {
    var s, l = n(this), u;
    return l == null ? void this.removeAttributeNS(e.space, e.local) : (s = this.getAttributeNS(e.space, e.local), u = l + "", s === u ? null : s === r && u === o ? i : (o = u, i = t(r = s, l)));
  };
}
function C1(e, t) {
  var n = Ps(e), r = n === "transform" ? i1 : qp;
  return this.attrTween(e, typeof t == "function" ? (n.local ? N1 : k1)(n, r, $a(this, "attr." + e, t)) : t == null ? (n.local ? S1 : x1)(n) : (n.local ? E1 : _1)(n, r, t));
}
function M1(e, t) {
  return function(n) {
    this.setAttribute(e, t.call(this, n));
  };
}
function P1(e, t) {
  return function(n) {
    this.setAttributeNS(e.space, e.local, t.call(this, n));
  };
}
function z1(e, t) {
  var n, r;
  function o() {
    var i = t.apply(this, arguments);
    return i !== r && (n = (r = i) && P1(e, i)), n;
  }
  return o._value = t, o;
}
function I1(e, t) {
  var n, r;
  function o() {
    var i = t.apply(this, arguments);
    return i !== r && (n = (r = i) && M1(e, i)), n;
  }
  return o._value = t, o;
}
function T1(e, t) {
  var n = "attr." + e;
  if (arguments.length < 2) return (n = this.tween(n)) && n._value;
  if (t == null) return this.tween(n, null);
  if (typeof t != "function") throw new Error();
  var r = Ps(e);
  return this.tween(n, (r.local ? z1 : I1)(r, t));
}
function L1(e, t) {
  return function() {
    Aa(this, e).delay = +t.apply(this, arguments);
  };
}
function A1(e, t) {
  return t = +t, function() {
    Aa(this, e).delay = t;
  };
}
function $1(e) {
  var t = this._id;
  return arguments.length ? this.each((typeof e == "function" ? L1 : A1)(t, e)) : pt(this.node(), t).delay;
}
function D1(e, t) {
  return function() {
    Et(this, e).duration = +t.apply(this, arguments);
  };
}
function R1(e, t) {
  return t = +t, function() {
    Et(this, e).duration = t;
  };
}
function j1(e) {
  var t = this._id;
  return arguments.length ? this.each((typeof e == "function" ? D1 : R1)(t, e)) : pt(this.node(), t).duration;
}
function O1(e, t) {
  if (typeof t != "function") throw new Error();
  return function() {
    Et(this, e).ease = t;
  };
}
function F1(e) {
  var t = this._id;
  return arguments.length ? this.each(O1(t, e)) : pt(this.node(), t).ease;
}
function H1(e, t) {
  return function() {
    var n = t.apply(this, arguments);
    if (typeof n != "function") throw new Error();
    Et(this, e).ease = n;
  };
}
function V1(e) {
  if (typeof e != "function") throw new Error();
  return this.each(H1(this._id, e));
}
function B1(e) {
  typeof e != "function" && (e = zp(e));
  for (var t = this._groups, n = t.length, r = new Array(n), o = 0; o < n; ++o)
    for (var i = t[o], s = i.length, l = r[o] = [], u, a = 0; a < s; ++a)
      (u = i[a]) && e.call(u, u.__data__, a, i) && l.push(u);
  return new Rt(r, this._parents, this._name, this._id);
}
function U1(e) {
  if (e._id !== this._id) throw new Error();
  for (var t = this._groups, n = e._groups, r = t.length, o = n.length, i = Math.min(r, o), s = new Array(r), l = 0; l < i; ++l)
    for (var u = t[l], a = n[l], d = u.length, c = s[l] = new Array(d), f, m = 0; m < d; ++m)
      (f = u[m] || a[m]) && (c[m] = f);
  for (; l < r; ++l)
    s[l] = t[l];
  return new Rt(s, this._parents, this._name, this._id);
}
function W1(e) {
  return (e + "").trim().split(/^|\s+/).every(function(t) {
    var n = t.indexOf(".");
    return n >= 0 && (t = t.slice(0, n)), !t || t === "start";
  });
}
function Y1(e, t, n) {
  var r, o, i = W1(t) ? Aa : Et;
  return function() {
    var s = i(this, e), l = s.on;
    l !== r && (o = (r = l).copy()).on(t, n), s.on = o;
  };
}
function X1(e, t) {
  var n = this._id;
  return arguments.length < 2 ? pt(this.node(), n).on.on(e) : this.each(Y1(n, e, t));
}
function Q1(e) {
  return function() {
    var t = this.parentNode;
    for (var n in this.__transition) if (+n !== e) return;
    t && t.removeChild(this);
  };
}
function b1() {
  return this.on("end.remove", Q1(this._id));
}
function K1(e) {
  var t = this._name, n = this._id;
  typeof e != "function" && (e = Pa(e));
  for (var r = this._groups, o = r.length, i = new Array(o), s = 0; s < o; ++s)
    for (var l = r[s], u = l.length, a = i[s] = new Array(u), d, c, f = 0; f < u; ++f)
      (d = l[f]) && (c = e.call(d, d.__data__, f, l)) && ("__data__" in d && (c.__data__ = d.__data__), a[f] = c, Is(a[f], t, n, f, a, pt(d, n)));
  return new Rt(i, this._parents, t, n);
}
function G1(e) {
  var t = this._name, n = this._id;
  typeof e != "function" && (e = Pp(e));
  for (var r = this._groups, o = r.length, i = [], s = [], l = 0; l < o; ++l)
    for (var u = r[l], a = u.length, d, c = 0; c < a; ++c)
      if (d = u[c]) {
        for (var f = e.call(d, d.__data__, c, u), m, y = pt(d, n), w = 0, x = f.length; w < x; ++w)
          (m = f[w]) && Is(m, t, n, w, f, y);
        i.push(f), s.push(d);
      }
  return new Rt(i, s, t, n);
}
var Z1 = $o.prototype.constructor;
function q1() {
  return new Z1(this._groups, this._parents);
}
function J1(e, t) {
  var n, r, o;
  return function() {
    var i = dr(this, e), s = (this.style.removeProperty(e), dr(this, e));
    return i === s ? null : i === n && s === r ? o : o = t(n = i, r = s);
  };
}
function Jp(e) {
  return function() {
    this.style.removeProperty(e);
  };
}
function eS(e, t, n) {
  var r, o = n + "", i;
  return function() {
    var s = dr(this, e);
    return s === o ? null : s === r ? i : i = t(r = s, n);
  };
}
function tS(e, t, n) {
  var r, o, i;
  return function() {
    var s = dr(this, e), l = n(this), u = l + "";
    return l == null && (u = l = (this.style.removeProperty(e), dr(this, e))), s === u ? null : s === r && u === o ? i : (o = u, i = t(r = s, l));
  };
}
function nS(e, t) {
  var n, r, o, i = "style." + t, s = "end." + i, l;
  return function() {
    var u = Et(this, e), a = u.on, d = u.value[i] == null ? l || (l = Jp(t)) : void 0;
    (a !== n || o !== d) && (r = (n = a).copy()).on(s, o = d), u.on = r;
  };
}
function rS(e, t, n) {
  var r = (e += "") == "transform" ? o1 : qp;
  return t == null ? this.styleTween(e, J1(e, r)).on("end.style." + e, Jp(e)) : typeof t == "function" ? this.styleTween(e, tS(e, r, $a(this, "style." + e, t))).each(nS(this._id, e)) : this.styleTween(e, eS(e, r, t), n).on("end.style." + e, null);
}
function oS(e, t, n) {
  return function(r) {
    this.style.setProperty(e, t.call(this, r), n);
  };
}
function iS(e, t, n) {
  var r, o;
  function i() {
    var s = t.apply(this, arguments);
    return s !== o && (r = (o = s) && oS(e, s, n)), r;
  }
  return i._value = t, i;
}
function sS(e, t, n) {
  var r = "style." + (e += "");
  if (arguments.length < 2) return (r = this.tween(r)) && r._value;
  if (t == null) return this.tween(r, null);
  if (typeof t != "function") throw new Error();
  return this.tween(r, iS(e, t, n ?? ""));
}
function lS(e) {
  return function() {
    this.textContent = e;
  };
}
function uS(e) {
  return function() {
    var t = e(this);
    this.textContent = t ?? "";
  };
}
function aS(e) {
  return this.tween("text", typeof e == "function" ? uS($a(this, "text", e)) : lS(e == null ? "" : e + ""));
}
function cS(e) {
  return function(t) {
    this.textContent = e.call(this, t);
  };
}
function fS(e) {
  var t, n;
  function r() {
    var o = e.apply(this, arguments);
    return o !== n && (t = (n = o) && cS(o)), t;
  }
  return r._value = e, r;
}
function dS(e) {
  var t = "text";
  if (arguments.length < 1) return (t = this.tween(t)) && t._value;
  if (e == null) return this.tween(t, null);
  if (typeof e != "function") throw new Error();
  return this.tween(t, fS(e));
}
function hS() {
  for (var e = this._name, t = this._id, n = eg(), r = this._groups, o = r.length, i = 0; i < o; ++i)
    for (var s = r[i], l = s.length, u, a = 0; a < l; ++a)
      if (u = s[a]) {
        var d = pt(u, t);
        Is(u, e, n, a, s, {
          time: d.time + d.delay + d.duration,
          delay: 0,
          duration: d.duration,
          ease: d.ease
        });
      }
  return new Rt(r, this._parents, e, n);
}
function pS() {
  var e, t, n = this, r = n._id, o = n.size();
  return new Promise(function(i, s) {
    var l = { value: s }, u = { value: function() {
      --o === 0 && i();
    } };
    n.each(function() {
      var a = Et(this, r), d = a.on;
      d !== e && (t = (e = d).copy(), t._.cancel.push(l), t._.interrupt.push(l), t._.end.push(u)), a.on = t;
    }), o === 0 && i();
  });
}
var gS = 0;
function Rt(e, t, n, r) {
  this._groups = e, this._parents = t, this._name = n, this._id = r;
}
function eg() {
  return ++gS;
}
var Nt = $o.prototype;
Rt.prototype = {
  constructor: Rt,
  select: K1,
  selectAll: G1,
  selectChild: Nt.selectChild,
  selectChildren: Nt.selectChildren,
  filter: B1,
  merge: U1,
  selection: q1,
  transition: hS,
  call: Nt.call,
  nodes: Nt.nodes,
  node: Nt.node,
  size: Nt.size,
  empty: Nt.empty,
  each: Nt.each,
  on: X1,
  attr: C1,
  attrTween: T1,
  style: rS,
  styleTween: sS,
  text: aS,
  textTween: dS,
  remove: b1,
  tween: w1,
  delay: $1,
  duration: j1,
  ease: F1,
  easeVarying: V1,
  end: pS,
  [Symbol.iterator]: Nt[Symbol.iterator]
};
function mS(e) {
  return ((e *= 2) <= 1 ? e * e * e : (e -= 2) * e * e + 2) / 2;
}
var yS = {
  time: null,
  // Set on use.
  delay: 0,
  duration: 250,
  ease: mS
};
function vS(e, t) {
  for (var n; !(n = e.__transition) || !(n = n[t]); )
    if (!(e = e.parentNode))
      throw new Error(`transition ${t} not found`);
  return n;
}
function wS(e) {
  var t, n;
  e instanceof Rt ? (t = e._id, e = e._name) : (t = eg(), (n = yS).time = La(), e = e == null ? null : e + "");
  for (var r = this._groups, o = r.length, i = 0; i < o; ++i)
    for (var s = r[i], l = s.length, u, a = 0; a < l; ++a)
      (u = s[a]) && Is(u, e, t, a, s, n || vS(u, t));
  return new Rt(r, this._parents, e, t);
}
$o.prototype.interrupt = m1;
$o.prototype.transition = wS;
const ai = (e) => () => e;
function xS(e, {
  sourceEvent: t,
  target: n,
  transform: r,
  dispatch: o
}) {
  Object.defineProperties(this, {
    type: { value: e, enumerable: !0, configurable: !0 },
    sourceEvent: { value: t, enumerable: !0, configurable: !0 },
    target: { value: n, enumerable: !0, configurable: !0 },
    transform: { value: r, enumerable: !0, configurable: !0 },
    _: { value: o }
  });
}
function It(e, t, n) {
  this.k = e, this.x = t, this.y = n;
}
It.prototype = {
  constructor: It,
  scale: function(e) {
    return e === 1 ? this : new It(this.k * e, this.x, this.y);
  },
  translate: function(e, t) {
    return e === 0 & t === 0 ? this : new It(this.k, this.x + this.k * e, this.y + this.k * t);
  },
  apply: function(e) {
    return [e[0] * this.k + this.x, e[1] * this.k + this.y];
  },
  applyX: function(e) {
    return e * this.k + this.x;
  },
  applyY: function(e) {
    return e * this.k + this.y;
  },
  invert: function(e) {
    return [(e[0] - this.x) / this.k, (e[1] - this.y) / this.k];
  },
  invertX: function(e) {
    return (e - this.x) / this.k;
  },
  invertY: function(e) {
    return (e - this.y) / this.k;
  },
  rescaleX: function(e) {
    return e.copy().domain(e.range().map(this.invertX, this).map(e.invert, e));
  },
  rescaleY: function(e) {
    return e.copy().domain(e.range().map(this.invertY, this).map(e.invert, e));
  },
  toString: function() {
    return "translate(" + this.x + "," + this.y + ") scale(" + this.k + ")";
  }
};
var Ts = new It(1, 0, 0);
tg.prototype = It.prototype;
function tg(e) {
  for (; !e.__zoom; ) if (!(e = e.parentNode)) return Ts;
  return e.__zoom;
}
function wl(e) {
  e.stopImmediatePropagation();
}
function jr(e) {
  e.preventDefault(), e.stopImmediatePropagation();
}
function SS(e) {
  return (!e.ctrlKey || e.type === "wheel") && !e.button;
}
function _S() {
  var e = this;
  return e instanceof SVGElement ? (e = e.ownerSVGElement || e, e.hasAttribute("viewBox") ? (e = e.viewBox.baseVal, [[e.x, e.y], [e.x + e.width, e.y + e.height]]) : [[0, 0], [e.width.baseVal.value, e.height.baseVal.value]]) : [[0, 0], [e.clientWidth, e.clientHeight]];
}
function Cf() {
  return this.__zoom || Ts;
}
function ES(e) {
  return -e.deltaY * (e.deltaMode === 1 ? 0.05 : e.deltaMode ? 1 : 2e-3) * (e.ctrlKey ? 10 : 1);
}
function kS() {
  return navigator.maxTouchPoints || "ontouchstart" in this;
}
function NS(e, t, n) {
  var r = e.invertX(t[0][0]) - n[0][0], o = e.invertX(t[1][0]) - n[1][0], i = e.invertY(t[0][1]) - n[0][1], s = e.invertY(t[1][1]) - n[1][1];
  return e.translate(
    o > r ? (r + o) / 2 : Math.min(0, r) || Math.max(0, o),
    s > i ? (i + s) / 2 : Math.min(0, i) || Math.max(0, s)
  );
}
function ng() {
  var e = SS, t = _S, n = NS, r = ES, o = kS, i = [0, 1 / 0], s = [[-1 / 0, -1 / 0], [1 / 0, 1 / 0]], l = 250, u = Mi, a = Ms("start", "zoom", "end"), d, c, f, m = 500, y = 150, w = 0, x = 10;
  function p(k) {
    k.property("__zoom", Cf).on("wheel.zoom", P, { passive: !1 }).on("mousedown.zoom", T).on("dblclick.zoom", j).filter(o).on("touchstart.zoom", I).on("touchmove.zoom", D).on("touchend.zoom touchcancel.zoom", H).style("-webkit-tap-highlight-color", "rgba(0,0,0,0)");
  }
  p.transform = function(k, A, z, $) {
    var N = k.selection ? k.selection() : k;
    N.property("__zoom", Cf), k !== N ? S(k, A, z, $) : N.interrupt().each(function() {
      E(this, arguments).event($).start().zoom(null, typeof A == "function" ? A.apply(this, arguments) : A).end();
    });
  }, p.scaleBy = function(k, A, z, $) {
    p.scaleTo(k, function() {
      var N = this.__zoom.k, C = typeof A == "function" ? A.apply(this, arguments) : A;
      return N * C;
    }, z, $);
  }, p.scaleTo = function(k, A, z, $) {
    p.transform(k, function() {
      var N = t.apply(this, arguments), C = this.__zoom, L = z == null ? v(N) : typeof z == "function" ? z.apply(this, arguments) : z, O = C.invert(L), R = typeof A == "function" ? A.apply(this, arguments) : A;
      return n(h(g(C, R), L, O), N, s);
    }, z, $);
  }, p.translateBy = function(k, A, z, $) {
    p.transform(k, function() {
      return n(this.__zoom.translate(
        typeof A == "function" ? A.apply(this, arguments) : A,
        typeof z == "function" ? z.apply(this, arguments) : z
      ), t.apply(this, arguments), s);
    }, null, $);
  }, p.translateTo = function(k, A, z, $, N) {
    p.transform(k, function() {
      var C = t.apply(this, arguments), L = this.__zoom, O = $ == null ? v(C) : typeof $ == "function" ? $.apply(this, arguments) : $;
      return n(Ts.translate(O[0], O[1]).scale(L.k).translate(
        typeof A == "function" ? -A.apply(this, arguments) : -A,
        typeof z == "function" ? -z.apply(this, arguments) : -z
      ), C, s);
    }, $, N);
  };
  function g(k, A) {
    return A = Math.max(i[0], Math.min(i[1], A)), A === k.k ? k : new It(A, k.x, k.y);
  }
  function h(k, A, z) {
    var $ = A[0] - z[0] * k.k, N = A[1] - z[1] * k.k;
    return $ === k.x && N === k.y ? k : new It(k.k, $, N);
  }
  function v(k) {
    return [(+k[0][0] + +k[1][0]) / 2, (+k[0][1] + +k[1][1]) / 2];
  }
  function S(k, A, z, $) {
    k.on("start.zoom", function() {
      E(this, arguments).event($).start();
    }).on("interrupt.zoom end.zoom", function() {
      E(this, arguments).event($).end();
    }).tween("zoom", function() {
      var N = this, C = arguments, L = E(N, C).event($), O = t.apply(N, C), R = z == null ? v(O) : typeof z == "function" ? z.apply(N, C) : z, W = Math.max(O[1][0] - O[0][0], O[1][1] - O[0][1]), U = N.__zoom, X = typeof A == "function" ? A.apply(N, C) : A, Q = u(U.invert(R).concat(W / U.k), X.invert(R).concat(W / X.k));
      return function(K) {
        if (K === 1) K = X;
        else {
          var B = Q(K), G = W / B[2];
          K = new It(G, R[0] - B[0] * G, R[1] - B[1] * G);
        }
        L.zoom(null, K);
      };
    });
  }
  function E(k, A, z) {
    return !z && k.__zooming || new M(k, A);
  }
  function M(k, A) {
    this.that = k, this.args = A, this.active = 0, this.sourceEvent = null, this.extent = t.apply(k, A), this.taps = 0;
  }
  M.prototype = {
    event: function(k) {
      return k && (this.sourceEvent = k), this;
    },
    start: function() {
      return ++this.active === 1 && (this.that.__zooming = this, this.emit("start")), this;
    },
    zoom: function(k, A) {
      return this.mouse && k !== "mouse" && (this.mouse[1] = A.invert(this.mouse[0])), this.touch0 && k !== "touch" && (this.touch0[1] = A.invert(this.touch0[0])), this.touch1 && k !== "touch" && (this.touch1[1] = A.invert(this.touch1[0])), this.that.__zoom = A, this.emit("zoom"), this;
    },
    end: function() {
      return --this.active === 0 && (delete this.that.__zooming, this.emit("end")), this;
    },
    emit: function(k) {
      var A = We(this.that).datum();
      a.call(
        k,
        this.that,
        new xS(k, {
          sourceEvent: this.sourceEvent,
          target: p,
          transform: this.that.__zoom,
          dispatch: a
        }),
        A
      );
    }
  };
  function P(k, ...A) {
    if (!e.apply(this, arguments)) return;
    var z = E(this, A).event(k), $ = this.__zoom, N = Math.max(i[0], Math.min(i[1], $.k * Math.pow(2, r.apply(this, arguments)))), C = st(k);
    if (z.wheel)
      (z.mouse[0][0] !== C[0] || z.mouse[0][1] !== C[1]) && (z.mouse[1] = $.invert(z.mouse[0] = C)), clearTimeout(z.wheel);
    else {
      if ($.k === N) return;
      z.mouse = [C, $.invert(C)], Ii(this), z.start();
    }
    jr(k), z.wheel = setTimeout(L, y), z.zoom("mouse", n(h(g($, N), z.mouse[0], z.mouse[1]), z.extent, s));
    function L() {
      z.wheel = null, z.end();
    }
  }
  function T(k, ...A) {
    if (f || !e.apply(this, arguments)) return;
    var z = k.currentTarget, $ = E(this, A, !0).event(k), N = We(k.view).on("mousemove.zoom", R, !0).on("mouseup.zoom", W, !0), C = st(k, z), L = k.clientX, O = k.clientY;
    Fp(k.view), wl(k), $.mouse = [C, this.__zoom.invert(C)], Ii(this), $.start();
    function R(U) {
      if (jr(U), !$.moved) {
        var X = U.clientX - L, Q = U.clientY - O;
        $.moved = X * X + Q * Q > w;
      }
      $.event(U).zoom("mouse", n(h($.that.__zoom, $.mouse[0] = st(U, z), $.mouse[1]), $.extent, s));
    }
    function W(U) {
      N.on("mousemove.zoom mouseup.zoom", null), Hp(U.view, $.moved), jr(U), $.event(U).end();
    }
  }
  function j(k, ...A) {
    if (e.apply(this, arguments)) {
      var z = this.__zoom, $ = st(k.changedTouches ? k.changedTouches[0] : k, this), N = z.invert($), C = z.k * (k.shiftKey ? 0.5 : 2), L = n(h(g(z, C), $, N), t.apply(this, A), s);
      jr(k), l > 0 ? We(this).transition().duration(l).call(S, L, $, k) : We(this).call(p.transform, L, $, k);
    }
  }
  function I(k, ...A) {
    if (e.apply(this, arguments)) {
      var z = k.touches, $ = z.length, N = E(this, A, k.changedTouches.length === $).event(k), C, L, O, R;
      for (wl(k), L = 0; L < $; ++L)
        O = z[L], R = st(O, this), R = [R, this.__zoom.invert(R), O.identifier], N.touch0 ? !N.touch1 && N.touch0[2] !== R[2] && (N.touch1 = R, N.taps = 0) : (N.touch0 = R, C = !0, N.taps = 1 + !!d);
      d && (d = clearTimeout(d)), C && (N.taps < 2 && (c = R[0], d = setTimeout(function() {
        d = null;
      }, m)), Ii(this), N.start());
    }
  }
  function D(k, ...A) {
    if (this.__zooming) {
      var z = E(this, A).event(k), $ = k.changedTouches, N = $.length, C, L, O, R;
      for (jr(k), C = 0; C < N; ++C)
        L = $[C], O = st(L, this), z.touch0 && z.touch0[2] === L.identifier ? z.touch0[0] = O : z.touch1 && z.touch1[2] === L.identifier && (z.touch1[0] = O);
      if (L = z.that.__zoom, z.touch1) {
        var W = z.touch0[0], U = z.touch0[1], X = z.touch1[0], Q = z.touch1[1], K = (K = X[0] - W[0]) * K + (K = X[1] - W[1]) * K, B = (B = Q[0] - U[0]) * B + (B = Q[1] - U[1]) * B;
        L = g(L, Math.sqrt(K / B)), O = [(W[0] + X[0]) / 2, (W[1] + X[1]) / 2], R = [(U[0] + Q[0]) / 2, (U[1] + Q[1]) / 2];
      } else if (z.touch0) O = z.touch0[0], R = z.touch0[1];
      else return;
      z.zoom("touch", n(h(L, O, R), z.extent, s));
    }
  }
  function H(k, ...A) {
    if (this.__zooming) {
      var z = E(this, A).event(k), $ = k.changedTouches, N = $.length, C, L;
      for (wl(k), f && clearTimeout(f), f = setTimeout(function() {
        f = null;
      }, m), C = 0; C < N; ++C)
        L = $[C], z.touch0 && z.touch0[2] === L.identifier ? delete z.touch0 : z.touch1 && z.touch1[2] === L.identifier && delete z.touch1;
      if (z.touch1 && !z.touch0 && (z.touch0 = z.touch1, delete z.touch1), z.touch0) z.touch0[1] = this.__zoom.invert(z.touch0[0]);
      else if (z.end(), z.taps === 2 && (L = st(L, this), Math.hypot(c[0] - L[0], c[1] - L[1]) < x)) {
        var O = We(this).on("dblclick.zoom");
        O && O.apply(this, arguments);
      }
    }
  }
  return p.wheelDelta = function(k) {
    return arguments.length ? (r = typeof k == "function" ? k : ai(+k), p) : r;
  }, p.filter = function(k) {
    return arguments.length ? (e = typeof k == "function" ? k : ai(!!k), p) : e;
  }, p.touchable = function(k) {
    return arguments.length ? (o = typeof k == "function" ? k : ai(!!k), p) : o;
  }, p.extent = function(k) {
    return arguments.length ? (t = typeof k == "function" ? k : ai([[+k[0][0], +k[0][1]], [+k[1][0], +k[1][1]]]), p) : t;
  }, p.scaleExtent = function(k) {
    return arguments.length ? (i[0] = +k[0], i[1] = +k[1], p) : [i[0], i[1]];
  }, p.translateExtent = function(k) {
    return arguments.length ? (s[0][0] = +k[0][0], s[1][0] = +k[1][0], s[0][1] = +k[0][1], s[1][1] = +k[1][1], p) : [[s[0][0], s[0][1]], [s[1][0], s[1][1]]];
  }, p.constrain = function(k) {
    return arguments.length ? (n = k, p) : n;
  }, p.duration = function(k) {
    return arguments.length ? (l = +k, p) : l;
  }, p.interpolate = function(k) {
    return arguments.length ? (u = k, p) : u;
  }, p.on = function() {
    var k = a.on.apply(a, arguments);
    return k === a ? p : k;
  }, p.clickDistance = function(k) {
    return arguments.length ? (w = (k = +k) * k, p) : Math.sqrt(w);
  }, p.tapDistance = function(k) {
    return arguments.length ? (x = +k, p) : x;
  }, p;
}
const _t = {
  error001: () => "[React Flow]: Seems like you have not used zustand provider as an ancestor. Help: https://reactflow.dev/error#001",
  error002: () => "It looks like you've created a new nodeTypes or edgeTypes object. If this wasn't on purpose please define the nodeTypes/edgeTypes outside of the component or memoize them.",
  error003: (e) => `Node type "${e}" not found. Using fallback type "default".`,
  error004: () => "The React Flow parent container needs a width and a height to render the graph.",
  error005: () => "Only child nodes can use a parent extent.",
  error006: () => "Can't create edge. An edge needs a source and a target.",
  error007: (e) => `The old edge with id=${e} does not exist.`,
  error009: (e) => `Marker type "${e}" doesn't exist.`,
  error008: (e, { id: t, sourceHandle: n, targetHandle: r }) => `Couldn't create edge for ${e} handle id: "${e === "source" ? n : r}", edge id: ${t}.`,
  error010: () => "Handle: No node id found. Make sure to only use a Handle inside a custom Node.",
  error011: (e) => `Edge type "${e}" not found. Using fallback type "default".`,
  error012: (e) => `Node with id "${e}" does not exist, it may have been removed. This can happen when a node is deleted before the "onNodeClick" handler is called.`,
  error013: (e = "react") => `It seems that you haven't loaded the styles. Please import '@xyflow/${e}/dist/style.css' or base.css to make sure everything is working properly.`,
  error014: () => "useNodeConnections: No node ID found. Call useNodeConnections inside a custom Node or provide a node ID.",
  error015: () => "It seems that you are trying to drag a node that is not initialized. Please use onNodesChange as explained in the docs."
}, Eo = [
  [Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY],
  [Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY]
], rg = ["Enter", " ", "Escape"], og = {
  "node.a11yDescription.default": "Press enter or space to select a node. Press delete to remove it and escape to cancel.",
  "node.a11yDescription.keyboardDisabled": "Press enter or space to select a node. You can then use the arrow keys to move the node around. Press delete to remove it and escape to cancel.",
  "node.a11yDescription.ariaLiveMessage": ({ direction: e, x: t, y: n }) => `Moved selected node ${e}. New position, x: ${t}, y: ${n}`,
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
var pr;
(function(e) {
  e.Strict = "strict", e.Loose = "loose";
})(pr || (pr = {}));
var _n;
(function(e) {
  e.Free = "free", e.Vertical = "vertical", e.Horizontal = "horizontal";
})(_n || (_n = {}));
var ko;
(function(e) {
  e.Partial = "partial", e.Full = "full";
})(ko || (ko = {}));
const ig = {
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
var Qt;
(function(e) {
  e.Bezier = "default", e.Straight = "straight", e.Step = "step", e.SmoothStep = "smoothstep", e.SimpleBezier = "simplebezier";
})(Qt || (Qt = {}));
var No;
(function(e) {
  e.Arrow = "arrow", e.ArrowClosed = "arrowclosed";
})(No || (No = {}));
var b;
(function(e) {
  e.Left = "left", e.Top = "top", e.Right = "right", e.Bottom = "bottom";
})(b || (b = {}));
const Mf = {
  [b.Left]: b.Right,
  [b.Right]: b.Left,
  [b.Top]: b.Bottom,
  [b.Bottom]: b.Top
};
function sg(e) {
  return e === null ? null : e ? "valid" : "invalid";
}
const lg = (e) => "id" in e && "source" in e && "target" in e, CS = (e) => "id" in e && "position" in e && !("source" in e) && !("target" in e), Da = (e) => "id" in e && "internals" in e && !("source" in e) && !("target" in e), Ro = (e, t = [0, 0]) => {
  const { width: n, height: r } = Ot(e), o = e.origin ?? t, i = n * o[0], s = r * o[1];
  return {
    x: e.position.x - i,
    y: e.position.y - s
  };
}, MS = (e, t = { nodeOrigin: [0, 0] }) => {
  if (e.length === 0)
    return { x: 0, y: 0, width: 0, height: 0 };
  const n = e.reduce((r, o) => {
    const i = typeof o == "string";
    let s = !t.nodeLookup && !i ? o : void 0;
    t.nodeLookup && (s = i ? t.nodeLookup.get(o) : Da(o) ? o : t.nodeLookup.get(o.id));
    const l = s ? us(s, t.nodeOrigin) : { x: 0, y: 0, x2: 0, y2: 0 };
    return Ls(r, l);
  }, { x: 1 / 0, y: 1 / 0, x2: -1 / 0, y2: -1 / 0 });
  return As(n);
}, jo = (e, t = {}) => {
  let n = { x: 1 / 0, y: 1 / 0, x2: -1 / 0, y2: -1 / 0 }, r = !1;
  return e.forEach((o) => {
    (t.filter === void 0 || t.filter(o)) && (n = Ls(n, us(o)), r = !0);
  }), r ? As(n) : { x: 0, y: 0, width: 0, height: 0 };
}, Ra = (e, t, [n, r, o] = [0, 0, 1], i = !1, s = !1) => {
  const l = {
    ...Fo(t, [n, r, o]),
    width: t.width / o,
    height: t.height / o
  }, u = [];
  for (const a of e.values()) {
    const { measured: d, selectable: c = !0, hidden: f = !1 } = a;
    if (s && !c || f)
      continue;
    const m = d.width ?? a.width ?? a.initialWidth ?? null, y = d.height ?? a.height ?? a.initialHeight ?? null, w = Co(l, mr(a)), x = (m ?? 0) * (y ?? 0), p = i && w > 0;
    (!a.internals.handleBounds || p || w >= x || a.dragging) && u.push(a);
  }
  return u;
}, PS = (e, t) => {
  const n = /* @__PURE__ */ new Set();
  return e.forEach((r) => {
    n.add(r.id);
  }), t.filter((r) => n.has(r.source) || n.has(r.target));
};
function zS(e, t) {
  const n = /* @__PURE__ */ new Map(), r = t != null && t.nodes ? new Set(t.nodes.map((o) => o.id)) : null;
  return e.forEach((o) => {
    o.measured.width && o.measured.height && ((t == null ? void 0 : t.includeHiddenNodes) || !o.hidden) && (!r || r.has(o.id)) && n.set(o.id, o);
  }), n;
}
async function IS({ nodes: e, width: t, height: n, panZoom: r, minZoom: o, maxZoom: i }, s) {
  if (e.size === 0)
    return Promise.resolve(!0);
  const l = zS(e, s), u = jo(l), a = ja(u, t, n, (s == null ? void 0 : s.minZoom) ?? o, (s == null ? void 0 : s.maxZoom) ?? i, (s == null ? void 0 : s.padding) ?? 0.1);
  return await r.setViewport(a, {
    duration: s == null ? void 0 : s.duration,
    ease: s == null ? void 0 : s.ease,
    interpolate: s == null ? void 0 : s.interpolate
  }), Promise.resolve(!0);
}
function ug({ nodeId: e, nextPosition: t, nodeLookup: n, nodeOrigin: r = [0, 0], nodeExtent: o, onError: i }) {
  const s = n.get(e), l = s.parentId ? n.get(s.parentId) : void 0, { x: u, y: a } = l ? l.internals.positionAbsolute : { x: 0, y: 0 }, d = s.origin ?? r;
  let c = s.extent || o;
  if (s.extent === "parent" && !s.expandParent)
    if (!l)
      i == null || i("005", _t.error005());
    else {
      const m = l.measured.width, y = l.measured.height;
      m && y && (c = [
        [u, a],
        [u + m, a + y]
      ]);
    }
  else l && yr(s.extent) && (c = [
    [s.extent[0][0] + u, s.extent[0][1] + a],
    [s.extent[1][0] + u, s.extent[1][1] + a]
  ]);
  const f = yr(c) ? In(t, c, s.measured) : t;
  return (s.measured.width === void 0 || s.measured.height === void 0) && (i == null || i("015", _t.error015())), {
    position: {
      x: f.x - u + (s.measured.width ?? 0) * d[0],
      y: f.y - a + (s.measured.height ?? 0) * d[1]
    },
    positionAbsolute: f
  };
}
async function TS({ nodesToRemove: e = [], edgesToRemove: t = [], nodes: n, edges: r, onBeforeDelete: o }) {
  const i = new Set(e.map((f) => f.id)), s = [];
  for (const f of n) {
    if (f.deletable === !1)
      continue;
    const m = i.has(f.id), y = !m && f.parentId && s.find((w) => w.id === f.parentId);
    (m || y) && s.push(f);
  }
  const l = new Set(t.map((f) => f.id)), u = r.filter((f) => f.deletable !== !1), d = PS(s, u);
  for (const f of u)
    l.has(f.id) && !d.find((y) => y.id === f.id) && d.push(f);
  if (!o)
    return {
      edges: d,
      nodes: s
    };
  const c = await o({
    nodes: s,
    edges: d
  });
  return typeof c == "boolean" ? c ? { edges: d, nodes: s } : { edges: [], nodes: [] } : c;
}
const gr = (e, t = 0, n = 1) => Math.min(Math.max(e, t), n), In = (e = { x: 0, y: 0 }, t, n) => ({
  x: gr(e.x, t[0][0], t[1][0] - ((n == null ? void 0 : n.width) ?? 0)),
  y: gr(e.y, t[0][1], t[1][1] - ((n == null ? void 0 : n.height) ?? 0))
});
function ag(e, t, n) {
  const { width: r, height: o } = Ot(n), { x: i, y: s } = n.internals.positionAbsolute;
  return In(e, [
    [i, s],
    [i + r, s + o]
  ], t);
}
const Pf = (e, t, n) => e < t ? gr(Math.abs(e - t), 1, t) / t : e > n ? -gr(Math.abs(e - n), 1, t) / t : 0, cg = (e, t, n = 15, r = 40) => {
  const o = Pf(e.x, r, t.width - r) * n, i = Pf(e.y, r, t.height - r) * n;
  return [o, i];
}, Ls = (e, t) => ({
  x: Math.min(e.x, t.x),
  y: Math.min(e.y, t.y),
  x2: Math.max(e.x2, t.x2),
  y2: Math.max(e.y2, t.y2)
}), Cu = ({ x: e, y: t, width: n, height: r }) => ({
  x: e,
  y: t,
  x2: e + n,
  y2: t + r
}), As = ({ x: e, y: t, x2: n, y2: r }) => ({
  x: e,
  y: t,
  width: n - e,
  height: r - t
}), mr = (e, t = [0, 0]) => {
  var o, i;
  const { x: n, y: r } = Da(e) ? e.internals.positionAbsolute : Ro(e, t);
  return {
    x: n,
    y: r,
    width: ((o = e.measured) == null ? void 0 : o.width) ?? e.width ?? e.initialWidth ?? 0,
    height: ((i = e.measured) == null ? void 0 : i.height) ?? e.height ?? e.initialHeight ?? 0
  };
}, us = (e, t = [0, 0]) => {
  var o, i;
  const { x: n, y: r } = Da(e) ? e.internals.positionAbsolute : Ro(e, t);
  return {
    x: n,
    y: r,
    x2: n + (((o = e.measured) == null ? void 0 : o.width) ?? e.width ?? e.initialWidth ?? 0),
    y2: r + (((i = e.measured) == null ? void 0 : i.height) ?? e.height ?? e.initialHeight ?? 0)
  };
}, fg = (e, t) => As(Ls(Cu(e), Cu(t))), Co = (e, t) => {
  const n = Math.max(0, Math.min(e.x + e.width, t.x + t.width) - Math.max(e.x, t.x)), r = Math.max(0, Math.min(e.y + e.height, t.y + t.height) - Math.max(e.y, t.y));
  return Math.ceil(n * r);
}, zf = (e) => at(e.width) && at(e.height) && at(e.x) && at(e.y), at = (e) => !isNaN(e) && isFinite(e), LS = (e, t) => {
}, Oo = (e, t = [1, 1]) => ({
  x: t[0] * Math.round(e.x / t[0]),
  y: t[1] * Math.round(e.y / t[1])
}), Fo = ({ x: e, y: t }, [n, r, o], i = !1, s = [1, 1]) => {
  const l = {
    x: (e - n) / o,
    y: (t - r) / o
  };
  return i ? Oo(l, s) : l;
}, as = ({ x: e, y: t }, [n, r, o]) => ({
  x: e * o + n,
  y: t * o + r
});
function Rn(e, t) {
  if (typeof e == "number")
    return Math.floor((t - t / (1 + e)) * 0.5);
  if (typeof e == "string" && e.endsWith("px")) {
    const n = parseFloat(e);
    if (!Number.isNaN(n))
      return Math.floor(n);
  }
  if (typeof e == "string" && e.endsWith("%")) {
    const n = parseFloat(e);
    if (!Number.isNaN(n))
      return Math.floor(t * n * 0.01);
  }
  return console.error(`[React Flow] The padding value "${e}" is invalid. Please provide a number or a string with a valid unit (px or %).`), 0;
}
function AS(e, t, n) {
  if (typeof e == "string" || typeof e == "number") {
    const r = Rn(e, n), o = Rn(e, t);
    return {
      top: r,
      right: o,
      bottom: r,
      left: o,
      x: o * 2,
      y: r * 2
    };
  }
  if (typeof e == "object") {
    const r = Rn(e.top ?? e.y ?? 0, n), o = Rn(e.bottom ?? e.y ?? 0, n), i = Rn(e.left ?? e.x ?? 0, t), s = Rn(e.right ?? e.x ?? 0, t);
    return { top: r, right: s, bottom: o, left: i, x: i + s, y: r + o };
  }
  return { top: 0, right: 0, bottom: 0, left: 0, x: 0, y: 0 };
}
function $S(e, t, n, r, o, i) {
  const { x: s, y: l } = as(e, [t, n, r]), { x: u, y: a } = as({ x: e.x + e.width, y: e.y + e.height }, [t, n, r]), d = o - u, c = i - a;
  return {
    left: Math.floor(s),
    top: Math.floor(l),
    right: Math.floor(d),
    bottom: Math.floor(c)
  };
}
const ja = (e, t, n, r, o, i) => {
  const s = AS(i, t, n), l = (t - s.x) / e.width, u = (n - s.y) / e.height, a = Math.min(l, u), d = gr(a, r, o), c = e.x + e.width / 2, f = e.y + e.height / 2, m = t / 2 - c * d, y = n / 2 - f * d, w = $S(e, m, y, d, t, n), x = {
    left: Math.min(w.left - s.left, 0),
    top: Math.min(w.top - s.top, 0),
    right: Math.min(w.right - s.right, 0),
    bottom: Math.min(w.bottom - s.bottom, 0)
  };
  return {
    x: m - x.left + x.right,
    y: y - x.top + x.bottom,
    zoom: d
  };
}, Mo = () => {
  var e;
  return typeof navigator < "u" && ((e = navigator == null ? void 0 : navigator.userAgent) == null ? void 0 : e.indexOf("Mac")) >= 0;
};
function yr(e) {
  return e != null && e !== "parent";
}
function Ot(e) {
  var t, n;
  return {
    width: ((t = e.measured) == null ? void 0 : t.width) ?? e.width ?? e.initialWidth ?? 0,
    height: ((n = e.measured) == null ? void 0 : n.height) ?? e.height ?? e.initialHeight ?? 0
  };
}
function dg(e) {
  var t, n;
  return (((t = e.measured) == null ? void 0 : t.width) ?? e.width ?? e.initialWidth) !== void 0 && (((n = e.measured) == null ? void 0 : n.height) ?? e.height ?? e.initialHeight) !== void 0;
}
function hg(e, t = { width: 0, height: 0 }, n, r, o) {
  const i = { ...e }, s = r.get(n);
  if (s) {
    const l = s.origin || o;
    i.x += s.internals.positionAbsolute.x - (t.width ?? 0) * l[0], i.y += s.internals.positionAbsolute.y - (t.height ?? 0) * l[1];
  }
  return i;
}
function If(e, t) {
  if (e.size !== t.size)
    return !1;
  for (const n of e)
    if (!t.has(n))
      return !1;
  return !0;
}
function DS() {
  let e, t;
  return { promise: new Promise((r, o) => {
    e = r, t = o;
  }), resolve: e, reject: t };
}
function RS(e) {
  return { ...og, ...e || {} };
}
function eo(e, { snapGrid: t = [0, 0], snapToGrid: n = !1, transform: r, containerBounds: o }) {
  const { x: i, y: s } = ct(e), l = Fo({ x: i - ((o == null ? void 0 : o.left) ?? 0), y: s - ((o == null ? void 0 : o.top) ?? 0) }, r), { x: u, y: a } = n ? Oo(l, t) : l;
  return {
    xSnapped: u,
    ySnapped: a,
    ...l
  };
}
const Oa = (e) => ({
  width: e.offsetWidth,
  height: e.offsetHeight
}), pg = (e) => {
  var t;
  return ((t = e == null ? void 0 : e.getRootNode) == null ? void 0 : t.call(e)) || (window == null ? void 0 : window.document);
}, jS = ["INPUT", "SELECT", "TEXTAREA"];
function gg(e) {
  var r, o;
  const t = ((o = (r = e.composedPath) == null ? void 0 : r.call(e)) == null ? void 0 : o[0]) || e.target;
  return (t == null ? void 0 : t.nodeType) !== 1 ? !1 : jS.includes(t.nodeName) || t.hasAttribute("contenteditable") || !!t.closest(".nokey");
}
const mg = (e) => "clientX" in e, ct = (e, t) => {
  var i, s;
  const n = mg(e), r = n ? e.clientX : (i = e.touches) == null ? void 0 : i[0].clientX, o = n ? e.clientY : (s = e.touches) == null ? void 0 : s[0].clientY;
  return {
    x: r - ((t == null ? void 0 : t.left) ?? 0),
    y: o - ((t == null ? void 0 : t.top) ?? 0)
  };
}, Tf = (e, t, n, r, o) => {
  const i = t.querySelectorAll(`.${e}`);
  return !i || !i.length ? null : Array.from(i).map((s) => {
    const l = s.getBoundingClientRect();
    return {
      id: s.getAttribute("data-handleid"),
      type: e,
      nodeId: o,
      position: s.getAttribute("data-handlepos"),
      x: (l.left - n.left) / r,
      y: (l.top - n.top) / r,
      ...Oa(s)
    };
  });
};
function yg({ sourceX: e, sourceY: t, targetX: n, targetY: r, sourceControlX: o, sourceControlY: i, targetControlX: s, targetControlY: l }) {
  const u = e * 0.125 + o * 0.375 + s * 0.375 + n * 0.125, a = t * 0.125 + i * 0.375 + l * 0.375 + r * 0.125, d = Math.abs(u - e), c = Math.abs(a - t);
  return [u, a, d, c];
}
function ci(e, t) {
  return e >= 0 ? 0.5 * e : t * 25 * Math.sqrt(-e);
}
function Lf({ pos: e, x1: t, y1: n, x2: r, y2: o, c: i }) {
  switch (e) {
    case b.Left:
      return [t - ci(t - r, i), n];
    case b.Right:
      return [t + ci(r - t, i), n];
    case b.Top:
      return [t, n - ci(n - o, i)];
    case b.Bottom:
      return [t, n + ci(o - n, i)];
  }
}
function vg({ sourceX: e, sourceY: t, sourcePosition: n = b.Bottom, targetX: r, targetY: o, targetPosition: i = b.Top, curvature: s = 0.25 }) {
  const [l, u] = Lf({
    pos: n,
    x1: e,
    y1: t,
    x2: r,
    y2: o,
    c: s
  }), [a, d] = Lf({
    pos: i,
    x1: r,
    y1: o,
    x2: e,
    y2: t,
    c: s
  }), [c, f, m, y] = yg({
    sourceX: e,
    sourceY: t,
    targetX: r,
    targetY: o,
    sourceControlX: l,
    sourceControlY: u,
    targetControlX: a,
    targetControlY: d
  });
  return [
    `M${e},${t} C${l},${u} ${a},${d} ${r},${o}`,
    c,
    f,
    m,
    y
  ];
}
function wg({ sourceX: e, sourceY: t, targetX: n, targetY: r }) {
  const o = Math.abs(n - e) / 2, i = n < e ? n + o : n - o, s = Math.abs(r - t) / 2, l = r < t ? r + s : r - s;
  return [i, l, o, s];
}
function OS({ sourceNode: e, targetNode: t, selected: n = !1, zIndex: r = 0, elevateOnSelect: o = !1, zIndexMode: i = "basic" }) {
  if (i === "manual")
    return r;
  const s = o && n ? r + 1e3 : r, l = Math.max(e.parentId || o && e.selected ? e.internals.z : 0, t.parentId || o && t.selected ? t.internals.z : 0);
  return s + l;
}
function FS({ sourceNode: e, targetNode: t, width: n, height: r, transform: o }) {
  const i = Ls(us(e), us(t));
  i.x === i.x2 && (i.x2 += 1), i.y === i.y2 && (i.y2 += 1);
  const s = {
    x: -o[0] / o[2],
    y: -o[1] / o[2],
    width: n / o[2],
    height: r / o[2]
  };
  return Co(s, As(i)) > 0;
}
const HS = ({ source: e, sourceHandle: t, target: n, targetHandle: r }) => `xy-edge__${e}${t || ""}-${n}${r || ""}`, VS = (e, t) => t.some((n) => n.source === e.source && n.target === e.target && (n.sourceHandle === e.sourceHandle || !n.sourceHandle && !e.sourceHandle) && (n.targetHandle === e.targetHandle || !n.targetHandle && !e.targetHandle)), BS = (e, t, n = {}) => {
  if (!e.source || !e.target)
    return t;
  const r = n.getEdgeId || HS;
  let o;
  return lg(e) ? o = { ...e } : o = {
    ...e,
    id: r(e)
  }, VS(o, t) ? t : (o.sourceHandle === null && delete o.sourceHandle, o.targetHandle === null && delete o.targetHandle, t.concat(o));
};
function xg({ sourceX: e, sourceY: t, targetX: n, targetY: r }) {
  const [o, i, s, l] = wg({
    sourceX: e,
    sourceY: t,
    targetX: n,
    targetY: r
  });
  return [`M ${e},${t}L ${n},${r}`, o, i, s, l];
}
const Af = {
  [b.Left]: { x: -1, y: 0 },
  [b.Right]: { x: 1, y: 0 },
  [b.Top]: { x: 0, y: -1 },
  [b.Bottom]: { x: 0, y: 1 }
}, US = ({ source: e, sourcePosition: t = b.Bottom, target: n }) => t === b.Left || t === b.Right ? e.x < n.x ? { x: 1, y: 0 } : { x: -1, y: 0 } : e.y < n.y ? { x: 0, y: 1 } : { x: 0, y: -1 }, $f = (e, t) => Math.sqrt(Math.pow(t.x - e.x, 2) + Math.pow(t.y - e.y, 2));
function WS({ source: e, sourcePosition: t = b.Bottom, target: n, targetPosition: r = b.Top, center: o, offset: i, stepPosition: s }) {
  const l = Af[t], u = Af[r], a = { x: e.x + l.x * i, y: e.y + l.y * i }, d = { x: n.x + u.x * i, y: n.y + u.y * i }, c = US({
    source: a,
    sourcePosition: t,
    target: d
  }), f = c.x !== 0 ? "x" : "y", m = c[f];
  let y = [], w, x;
  const p = { x: 0, y: 0 }, g = { x: 0, y: 0 }, [, , h, v] = wg({
    sourceX: e.x,
    sourceY: e.y,
    targetX: n.x,
    targetY: n.y
  });
  if (l[f] * u[f] === -1) {
    f === "x" ? (w = o.x ?? a.x + (d.x - a.x) * s, x = o.y ?? (a.y + d.y) / 2) : (w = o.x ?? (a.x + d.x) / 2, x = o.y ?? a.y + (d.y - a.y) * s);
    const P = [
      { x: w, y: a.y },
      { x: w, y: d.y }
    ], T = [
      { x: a.x, y: x },
      { x: d.x, y: x }
    ];
    l[f] === m ? y = f === "x" ? P : T : y = f === "x" ? T : P;
  } else {
    const P = [{ x: a.x, y: d.y }], T = [{ x: d.x, y: a.y }];
    if (f === "x" ? y = l.x === m ? T : P : y = l.y === m ? P : T, t === r) {
      const k = Math.abs(e[f] - n[f]);
      if (k <= i) {
        const A = Math.min(i - 1, i - k);
        l[f] === m ? p[f] = (a[f] > e[f] ? -1 : 1) * A : g[f] = (d[f] > n[f] ? -1 : 1) * A;
      }
    }
    if (t !== r) {
      const k = f === "x" ? "y" : "x", A = l[f] === u[k], z = a[k] > d[k], $ = a[k] < d[k];
      (l[f] === 1 && (!A && z || A && $) || l[f] !== 1 && (!A && $ || A && z)) && (y = f === "x" ? P : T);
    }
    const j = { x: a.x + p.x, y: a.y + p.y }, I = { x: d.x + g.x, y: d.y + g.y }, D = Math.max(Math.abs(j.x - y[0].x), Math.abs(I.x - y[0].x)), H = Math.max(Math.abs(j.y - y[0].y), Math.abs(I.y - y[0].y));
    D >= H ? (w = (j.x + I.x) / 2, x = y[0].y) : (w = y[0].x, x = (j.y + I.y) / 2);
  }
  const S = { x: a.x + p.x, y: a.y + p.y }, E = { x: d.x + g.x, y: d.y + g.y };
  return [[
    e,
    // we only want to add the gapped source/target if they are different from the first/last point to avoid duplicates which can cause issues with the bends
    ...S.x !== y[0].x || S.y !== y[0].y ? [S] : [],
    ...y,
    ...E.x !== y[y.length - 1].x || E.y !== y[y.length - 1].y ? [E] : [],
    n
  ], w, x, h, v];
}
function YS(e, t, n, r) {
  const o = Math.min($f(e, t) / 2, $f(t, n) / 2, r), { x: i, y: s } = t;
  if (e.x === i && i === n.x || e.y === s && s === n.y)
    return `L${i} ${s}`;
  if (e.y === s) {
    const a = e.x < n.x ? -1 : 1, d = e.y < n.y ? 1 : -1;
    return `L ${i + o * a},${s}Q ${i},${s} ${i},${s + o * d}`;
  }
  const l = e.x < n.x ? 1 : -1, u = e.y < n.y ? -1 : 1;
  return `L ${i},${s + o * u}Q ${i},${s} ${i + o * l},${s}`;
}
function Mu({ sourceX: e, sourceY: t, sourcePosition: n = b.Bottom, targetX: r, targetY: o, targetPosition: i = b.Top, borderRadius: s = 5, centerX: l, centerY: u, offset: a = 20, stepPosition: d = 0.5 }) {
  const [c, f, m, y, w] = WS({
    source: { x: e, y: t },
    sourcePosition: n,
    target: { x: r, y: o },
    targetPosition: i,
    center: { x: l, y: u },
    offset: a,
    stepPosition: d
  });
  let x = `M${c[0].x} ${c[0].y}`;
  for (let p = 1; p < c.length - 1; p++)
    x += YS(c[p - 1], c[p], c[p + 1], s);
  return x += `L${c[c.length - 1].x} ${c[c.length - 1].y}`, [x, f, m, y, w];
}
function Df(e) {
  var t;
  return e && !!(e.internals.handleBounds || (t = e.handles) != null && t.length) && !!(e.measured.width || e.width || e.initialWidth);
}
function XS(e) {
  var c;
  const { sourceNode: t, targetNode: n } = e;
  if (!Df(t) || !Df(n))
    return null;
  const r = t.internals.handleBounds || Rf(t.handles), o = n.internals.handleBounds || Rf(n.handles), i = jf((r == null ? void 0 : r.source) ?? [], e.sourceHandle), s = jf(
    // when connection type is loose we can define all handles as sources and connect source -> source
    e.connectionMode === pr.Strict ? (o == null ? void 0 : o.target) ?? [] : ((o == null ? void 0 : o.target) ?? []).concat((o == null ? void 0 : o.source) ?? []),
    e.targetHandle
  );
  if (!i || !s)
    return (c = e.onError) == null || c.call(e, "008", _t.error008(i ? "target" : "source", {
      id: e.id,
      sourceHandle: e.sourceHandle,
      targetHandle: e.targetHandle
    })), null;
  const l = (i == null ? void 0 : i.position) || b.Bottom, u = (s == null ? void 0 : s.position) || b.Top, a = Tn(t, i, l), d = Tn(n, s, u);
  return {
    sourceX: a.x,
    sourceY: a.y,
    targetX: d.x,
    targetY: d.y,
    sourcePosition: l,
    targetPosition: u
  };
}
function Rf(e) {
  if (!e)
    return null;
  const t = [], n = [];
  for (const r of e)
    r.width = r.width ?? 1, r.height = r.height ?? 1, r.type === "source" ? t.push(r) : r.type === "target" && n.push(r);
  return {
    source: t,
    target: n
  };
}
function Tn(e, t, n = b.Left, r = !1) {
  const o = ((t == null ? void 0 : t.x) ?? 0) + e.internals.positionAbsolute.x, i = ((t == null ? void 0 : t.y) ?? 0) + e.internals.positionAbsolute.y, { width: s, height: l } = t ?? Ot(e);
  if (r)
    return { x: o + s / 2, y: i + l / 2 };
  switch ((t == null ? void 0 : t.position) ?? n) {
    case b.Top:
      return { x: o + s / 2, y: i };
    case b.Right:
      return { x: o + s, y: i + l / 2 };
    case b.Bottom:
      return { x: o + s / 2, y: i + l };
    case b.Left:
      return { x: o, y: i + l / 2 };
  }
}
function jf(e, t) {
  return e && (t ? e.find((n) => n.id === t) : e[0]) || null;
}
function Pu(e, t) {
  return e ? typeof e == "string" ? e : `${t ? `${t}__` : ""}${Object.keys(e).sort().map((r) => `${r}=${e[r]}`).join("&")}` : "";
}
function QS(e, { id: t, defaultColor: n, defaultMarkerStart: r, defaultMarkerEnd: o }) {
  const i = /* @__PURE__ */ new Set();
  return e.reduce((s, l) => ([l.markerStart || r, l.markerEnd || o].forEach((u) => {
    if (u && typeof u == "object") {
      const a = Pu(u, t);
      i.has(a) || (s.push({ id: a, color: u.color || n, ...u }), i.add(a));
    }
  }), s), []).sort((s, l) => s.id.localeCompare(l.id));
}
const Sg = 1e3, bS = 10, Fa = {
  nodeOrigin: [0, 0],
  nodeExtent: Eo,
  elevateNodesOnSelect: !0,
  zIndexMode: "basic",
  defaults: {}
}, KS = {
  ...Fa,
  checkEquality: !0
};
function Ha(e, t) {
  const n = { ...e };
  for (const r in t)
    t[r] !== void 0 && (n[r] = t[r]);
  return n;
}
function GS(e, t, n) {
  const r = Ha(Fa, n);
  for (const o of e.values())
    if (o.parentId)
      Ba(o, e, t, r);
    else {
      const i = Ro(o, r.nodeOrigin), s = yr(o.extent) ? o.extent : r.nodeExtent, l = In(i, s, Ot(o));
      o.internals.positionAbsolute = l;
    }
}
function ZS(e, t) {
  if (!e.handles)
    return e.measured ? t == null ? void 0 : t.internals.handleBounds : void 0;
  const n = [], r = [];
  for (const o of e.handles) {
    const i = {
      id: o.id,
      width: o.width ?? 1,
      height: o.height ?? 1,
      nodeId: e.id,
      x: o.x,
      y: o.y,
      position: o.position,
      type: o.type
    };
    o.type === "source" ? n.push(i) : o.type === "target" && r.push(i);
  }
  return {
    source: n,
    target: r
  };
}
function Va(e) {
  return e === "manual";
}
function zu(e, t, n, r = {}) {
  var d, c;
  const o = Ha(KS, r), i = { i: 0 }, s = new Map(t), l = o != null && o.elevateNodesOnSelect && !Va(o.zIndexMode) ? Sg : 0;
  let u = e.length > 0, a = !1;
  t.clear(), n.clear();
  for (const f of e) {
    let m = s.get(f.id);
    if (o.checkEquality && f === (m == null ? void 0 : m.internals.userNode))
      t.set(f.id, m);
    else {
      const y = Ro(f, o.nodeOrigin), w = yr(f.extent) ? f.extent : o.nodeExtent, x = In(y, w, Ot(f));
      m = {
        ...o.defaults,
        ...f,
        measured: {
          width: (d = f.measured) == null ? void 0 : d.width,
          height: (c = f.measured) == null ? void 0 : c.height
        },
        internals: {
          positionAbsolute: x,
          // if user re-initializes the node or removes `measured` for whatever reason, we reset the handleBounds so that the node gets re-measured
          handleBounds: ZS(f, m),
          z: _g(f, l, o.zIndexMode),
          userNode: f
        }
      }, t.set(f.id, m);
    }
    (m.measured === void 0 || m.measured.width === void 0 || m.measured.height === void 0) && !m.hidden && (u = !1), f.parentId && Ba(m, t, n, r, i), a || (a = f.selected ?? !1);
  }
  return { nodesInitialized: u, hasSelectedNodes: a };
}
function qS(e, t) {
  if (!e.parentId)
    return;
  const n = t.get(e.parentId);
  n ? n.set(e.id, e) : t.set(e.parentId, /* @__PURE__ */ new Map([[e.id, e]]));
}
function Ba(e, t, n, r, o) {
  const { elevateNodesOnSelect: i, nodeOrigin: s, nodeExtent: l, zIndexMode: u } = Ha(Fa, r), a = e.parentId, d = t.get(a);
  if (!d) {
    console.warn(`Parent node ${a} not found. Please make sure that parent nodes are in front of their child nodes in the nodes array.`);
    return;
  }
  qS(e, n), o && !d.parentId && d.internals.rootParentIndex === void 0 && u === "auto" && (d.internals.rootParentIndex = ++o.i, d.internals.z = d.internals.z + o.i * bS), o && d.internals.rootParentIndex !== void 0 && (o.i = d.internals.rootParentIndex);
  const c = i && !Va(u) ? Sg : 0, { x: f, y: m, z: y } = JS(e, d, s, l, c, u), { positionAbsolute: w } = e.internals, x = f !== w.x || m !== w.y;
  (x || y !== e.internals.z) && t.set(e.id, {
    ...e,
    internals: {
      ...e.internals,
      positionAbsolute: x ? { x: f, y: m } : w,
      z: y
    }
  });
}
function _g(e, t, n) {
  const r = at(e.zIndex) ? e.zIndex : 0;
  return Va(n) ? r : r + (e.selected ? t : 0);
}
function JS(e, t, n, r, o, i) {
  const { x: s, y: l } = t.internals.positionAbsolute, u = Ot(e), a = Ro(e, n), d = yr(e.extent) ? In(a, e.extent, u) : a;
  let c = In({ x: s + d.x, y: l + d.y }, r, u);
  e.extent === "parent" && (c = ag(c, u, t));
  const f = _g(e, o, i), m = t.internals.z ?? 0;
  return {
    x: c.x,
    y: c.y,
    z: m >= f ? m + 1 : f
  };
}
function Ua(e, t, n, r = [0, 0]) {
  var s;
  const o = [], i = /* @__PURE__ */ new Map();
  for (const l of e) {
    const u = t.get(l.parentId);
    if (!u)
      continue;
    const a = ((s = i.get(l.parentId)) == null ? void 0 : s.expandedRect) ?? mr(u), d = fg(a, l.rect);
    i.set(l.parentId, { expandedRect: d, parent: u });
  }
  return i.size > 0 && i.forEach(({ expandedRect: l, parent: u }, a) => {
    var h;
    const d = u.internals.positionAbsolute, c = Ot(u), f = u.origin ?? r, m = l.x < d.x ? Math.round(Math.abs(d.x - l.x)) : 0, y = l.y < d.y ? Math.round(Math.abs(d.y - l.y)) : 0, w = Math.max(c.width, Math.round(l.width)), x = Math.max(c.height, Math.round(l.height)), p = (w - c.width) * f[0], g = (x - c.height) * f[1];
    (m > 0 || y > 0 || p || g) && (o.push({
      id: a,
      type: "position",
      position: {
        x: u.position.x - m + p,
        y: u.position.y - y + g
      }
    }), (h = n.get(a)) == null || h.forEach((v) => {
      e.some((S) => S.id === v.id) || o.push({
        id: v.id,
        type: "position",
        position: {
          x: v.position.x + m,
          y: v.position.y + y
        }
      });
    })), (c.width < l.width || c.height < l.height || m || y) && o.push({
      id: a,
      type: "dimensions",
      setAttributes: !0,
      dimensions: {
        width: w + (m ? f[0] * m - p : 0),
        height: x + (y ? f[1] * y - g : 0)
      }
    });
  }), o;
}
function e_(e, t, n, r, o, i, s) {
  const l = r == null ? void 0 : r.querySelector(".xyflow__viewport");
  let u = !1;
  if (!l)
    return { changes: [], updatedInternals: u };
  const a = [], d = window.getComputedStyle(l), { m22: c } = new window.DOMMatrixReadOnly(d.transform), f = [];
  for (const m of e.values()) {
    const y = t.get(m.id);
    if (!y)
      continue;
    if (y.hidden) {
      t.set(y.id, {
        ...y,
        internals: {
          ...y.internals,
          handleBounds: void 0
        }
      }), u = !0;
      continue;
    }
    const w = Oa(m.nodeElement), x = y.measured.width !== w.width || y.measured.height !== w.height;
    if (!!(w.width && w.height && (x || !y.internals.handleBounds || m.force))) {
      const g = m.nodeElement.getBoundingClientRect(), h = yr(y.extent) ? y.extent : i;
      let { positionAbsolute: v } = y.internals;
      y.parentId && y.extent === "parent" ? v = ag(v, w, t.get(y.parentId)) : h && (v = In(v, h, w));
      const S = {
        ...y,
        measured: w,
        internals: {
          ...y.internals,
          positionAbsolute: v,
          handleBounds: {
            source: Tf("source", m.nodeElement, g, c, y.id),
            target: Tf("target", m.nodeElement, g, c, y.id)
          }
        }
      };
      t.set(y.id, S), y.parentId && Ba(S, t, n, { nodeOrigin: o, zIndexMode: s }), u = !0, x && (a.push({
        id: y.id,
        type: "dimensions",
        dimensions: w
      }), y.expandParent && y.parentId && f.push({
        id: y.id,
        parentId: y.parentId,
        rect: mr(S, o)
      }));
    }
  }
  if (f.length > 0) {
    const m = Ua(f, t, n, o);
    a.push(...m);
  }
  return { changes: a, updatedInternals: u };
}
async function t_({ delta: e, panZoom: t, transform: n, translateExtent: r, width: o, height: i }) {
  if (!t || !e.x && !e.y)
    return Promise.resolve(!1);
  const s = await t.setViewportConstrained({
    x: n[0] + e.x,
    y: n[1] + e.y,
    zoom: n[2]
  }, [
    [0, 0],
    [o, i]
  ], r), l = !!s && (s.x !== n[0] || s.y !== n[1] || s.k !== n[2]);
  return Promise.resolve(l);
}
function Of(e, t, n, r, o, i) {
  let s = o;
  const l = r.get(s) || /* @__PURE__ */ new Map();
  r.set(s, l.set(n, t)), s = `${o}-${e}`;
  const u = r.get(s) || /* @__PURE__ */ new Map();
  if (r.set(s, u.set(n, t)), i) {
    s = `${o}-${e}-${i}`;
    const a = r.get(s) || /* @__PURE__ */ new Map();
    r.set(s, a.set(n, t));
  }
}
function Eg(e, t, n) {
  e.clear(), t.clear();
  for (const r of n) {
    const { source: o, target: i, sourceHandle: s = null, targetHandle: l = null } = r, u = { edgeId: r.id, source: o, target: i, sourceHandle: s, targetHandle: l }, a = `${o}-${s}--${i}-${l}`, d = `${i}-${l}--${o}-${s}`;
    Of("source", u, d, e, o, s), Of("target", u, a, e, i, l), t.set(r.id, r);
  }
}
function kg(e, t) {
  if (!e.parentId)
    return !1;
  const n = t.get(e.parentId);
  return n ? n.selected ? !0 : kg(n, t) : !1;
}
function Ff(e, t, n) {
  var o;
  let r = e;
  do {
    if ((o = r == null ? void 0 : r.matches) != null && o.call(r, t))
      return !0;
    if (r === n)
      return !1;
    r = r == null ? void 0 : r.parentElement;
  } while (r);
  return !1;
}
function n_(e, t, n, r) {
  const o = /* @__PURE__ */ new Map();
  for (const [i, s] of e)
    if ((s.selected || s.id === r) && (!s.parentId || !kg(s, e)) && (s.draggable || t && typeof s.draggable > "u")) {
      const l = e.get(i);
      l && o.set(i, {
        id: i,
        position: l.position || { x: 0, y: 0 },
        distance: {
          x: n.x - l.internals.positionAbsolute.x,
          y: n.y - l.internals.positionAbsolute.y
        },
        extent: l.extent,
        parentId: l.parentId,
        origin: l.origin,
        expandParent: l.expandParent,
        internals: {
          positionAbsolute: l.internals.positionAbsolute || { x: 0, y: 0 }
        },
        measured: {
          width: l.measured.width ?? 0,
          height: l.measured.height ?? 0
        }
      });
    }
  return o;
}
function xl({ nodeId: e, dragItems: t, nodeLookup: n, dragging: r = !0 }) {
  var s, l, u;
  const o = [];
  for (const [a, d] of t) {
    const c = (s = n.get(a)) == null ? void 0 : s.internals.userNode;
    c && o.push({
      ...c,
      position: d.position,
      dragging: r
    });
  }
  if (!e)
    return [o[0], o];
  const i = (l = n.get(e)) == null ? void 0 : l.internals.userNode;
  return [
    i ? {
      ...i,
      position: ((u = t.get(e)) == null ? void 0 : u.position) || i.position,
      dragging: r
    } : o[0],
    o
  ];
}
function r_({ dragItems: e, snapGrid: t, x: n, y: r }) {
  const o = e.values().next().value;
  if (!o)
    return null;
  const i = {
    x: n - o.distance.x,
    y: r - o.distance.y
  }, s = Oo(i, t);
  return {
    x: s.x - i.x,
    y: s.y - i.y
  };
}
function o_({ onNodeMouseDown: e, getStoreItems: t, onDragStart: n, onDrag: r, onDragStop: o }) {
  let i = { x: null, y: null }, s = 0, l = /* @__PURE__ */ new Map(), u = !1, a = { x: 0, y: 0 }, d = null, c = !1, f = null, m = !1, y = !1, w = null;
  function x({ noDragClassName: g, handleSelector: h, domNode: v, isSelectable: S, nodeId: E, nodeClickDistance: M = 0 }) {
    f = We(v);
    function P({ x: D, y: H }) {
      const { nodeLookup: k, nodeExtent: A, snapGrid: z, snapToGrid: $, nodeOrigin: N, onNodeDrag: C, onSelectionDrag: L, onError: O, updateNodePositions: R } = t();
      i = { x: D, y: H };
      let W = !1;
      const U = l.size > 1, X = U && A ? Cu(jo(l)) : null, Q = U && $ ? r_({
        dragItems: l,
        snapGrid: z,
        x: D,
        y: H
      }) : null;
      for (const [K, B] of l) {
        if (!k.has(K))
          continue;
        let G = { x: D - B.distance.x, y: H - B.distance.y };
        $ && (G = Q ? {
          x: Math.round(G.x + Q.x),
          y: Math.round(G.y + Q.y)
        } : Oo(G, z));
        let ee = null;
        if (U && A && !B.extent && X) {
          const { positionAbsolute: Z } = B.internals, ie = Z.x - X.x + A[0][0], ue = Z.x + B.measured.width - X.x2 + A[1][0], oe = Z.y - X.y + A[0][1], Pe = Z.y + B.measured.height - X.y2 + A[1][1];
          ee = [
            [ie, oe],
            [ue, Pe]
          ];
        }
        const { position: J, positionAbsolute: q } = ug({
          nodeId: K,
          nextPosition: G,
          nodeLookup: k,
          nodeExtent: ee || A,
          nodeOrigin: N,
          onError: O
        });
        W = W || B.position.x !== J.x || B.position.y !== J.y, B.position = J, B.internals.positionAbsolute = q;
      }
      if (y = y || W, !!W && (R(l, !0), w && (r || C || !E && L))) {
        const [K, B] = xl({
          nodeId: E,
          dragItems: l,
          nodeLookup: k
        });
        r == null || r(w, l, K, B), C == null || C(w, K, B), E || L == null || L(w, B);
      }
    }
    async function T() {
      if (!d)
        return;
      const { transform: D, panBy: H, autoPanSpeed: k, autoPanOnNodeDrag: A } = t();
      if (!A) {
        u = !1, cancelAnimationFrame(s);
        return;
      }
      const [z, $] = cg(a, d, k);
      (z !== 0 || $ !== 0) && (i.x = (i.x ?? 0) - z / D[2], i.y = (i.y ?? 0) - $ / D[2], await H({ x: z, y: $ }) && P(i)), s = requestAnimationFrame(T);
    }
    function j(D) {
      var U;
      const { nodeLookup: H, multiSelectionActive: k, nodesDraggable: A, transform: z, snapGrid: $, snapToGrid: N, selectNodesOnDrag: C, onNodeDragStart: L, onSelectionDragStart: O, unselectNodesAndEdges: R } = t();
      c = !0, (!C || !S) && !k && E && ((U = H.get(E)) != null && U.selected || R()), S && C && E && (e == null || e(E));
      const W = eo(D.sourceEvent, { transform: z, snapGrid: $, snapToGrid: N, containerBounds: d });
      if (i = W, l = n_(H, A, W, E), l.size > 0 && (n || L || !E && O)) {
        const [X, Q] = xl({
          nodeId: E,
          dragItems: l,
          nodeLookup: H
        });
        n == null || n(D.sourceEvent, l, X, Q), L == null || L(D.sourceEvent, X, Q), E || O == null || O(D.sourceEvent, Q);
      }
    }
    const I = Vp().clickDistance(M).on("start", (D) => {
      const { domNode: H, nodeDragThreshold: k, transform: A, snapGrid: z, snapToGrid: $ } = t();
      d = (H == null ? void 0 : H.getBoundingClientRect()) || null, m = !1, y = !1, w = D.sourceEvent, k === 0 && j(D), i = eo(D.sourceEvent, { transform: A, snapGrid: z, snapToGrid: $, containerBounds: d }), a = ct(D.sourceEvent, d);
    }).on("drag", (D) => {
      const { autoPanOnNodeDrag: H, transform: k, snapGrid: A, snapToGrid: z, nodeDragThreshold: $, nodeLookup: N } = t(), C = eo(D.sourceEvent, { transform: k, snapGrid: A, snapToGrid: z, containerBounds: d });
      if (w = D.sourceEvent, (D.sourceEvent.type === "touchmove" && D.sourceEvent.touches.length > 1 || // if user deletes a node while dragging, we need to abort the drag to prevent errors
      E && !N.has(E)) && (m = !0), !m) {
        if (!u && H && c && (u = !0, T()), !c) {
          const L = ct(D.sourceEvent, d), O = L.x - a.x, R = L.y - a.y;
          Math.sqrt(O * O + R * R) > $ && j(D);
        }
        (i.x !== C.xSnapped || i.y !== C.ySnapped) && l && c && (a = ct(D.sourceEvent, d), P(C));
      }
    }).on("end", (D) => {
      if (!(!c || m) && (u = !1, c = !1, cancelAnimationFrame(s), l.size > 0)) {
        const { nodeLookup: H, updateNodePositions: k, onNodeDragStop: A, onSelectionDragStop: z } = t();
        if (y && (k(l, !1), y = !1), o || A || !E && z) {
          const [$, N] = xl({
            nodeId: E,
            dragItems: l,
            nodeLookup: H,
            dragging: !1
          });
          o == null || o(D.sourceEvent, l, $, N), A == null || A(D.sourceEvent, $, N), E || z == null || z(D.sourceEvent, N);
        }
      }
    }).filter((D) => {
      const H = D.target;
      return !D.button && (!g || !Ff(H, `.${g}`, v)) && (!h || Ff(H, h, v));
    });
    f.call(I);
  }
  function p() {
    f == null || f.on(".drag", null);
  }
  return {
    update: x,
    destroy: p
  };
}
function i_(e, t, n) {
  const r = [], o = {
    x: e.x - n,
    y: e.y - n,
    width: n * 2,
    height: n * 2
  };
  for (const i of t.values())
    Co(o, mr(i)) > 0 && r.push(i);
  return r;
}
const s_ = 250;
function l_(e, t, n, r) {
  var l, u;
  let o = [], i = 1 / 0;
  const s = i_(e, n, t + s_);
  for (const a of s) {
    const d = [...((l = a.internals.handleBounds) == null ? void 0 : l.source) ?? [], ...((u = a.internals.handleBounds) == null ? void 0 : u.target) ?? []];
    for (const c of d) {
      if (r.nodeId === c.nodeId && r.type === c.type && r.id === c.id)
        continue;
      const { x: f, y: m } = Tn(a, c, c.position, !0), y = Math.sqrt(Math.pow(f - e.x, 2) + Math.pow(m - e.y, 2));
      y > t || (y < i ? (o = [{ ...c, x: f, y: m }], i = y) : y === i && o.push({ ...c, x: f, y: m }));
    }
  }
  if (!o.length)
    return null;
  if (o.length > 1) {
    const a = r.type === "source" ? "target" : "source";
    return o.find((d) => d.type === a) ?? o[0];
  }
  return o[0];
}
function Ng(e, t, n, r, o, i = !1) {
  var a, d, c;
  const s = r.get(e);
  if (!s)
    return null;
  const l = o === "strict" ? (a = s.internals.handleBounds) == null ? void 0 : a[t] : [...((d = s.internals.handleBounds) == null ? void 0 : d.source) ?? [], ...((c = s.internals.handleBounds) == null ? void 0 : c.target) ?? []], u = (n ? l == null ? void 0 : l.find((f) => f.id === n) : l == null ? void 0 : l[0]) ?? null;
  return u && i ? { ...u, ...Tn(s, u, u.position, !0) } : u;
}
function Cg(e, t) {
  return e || (t != null && t.classList.contains("target") ? "target" : t != null && t.classList.contains("source") ? "source" : null);
}
function u_(e, t) {
  let n = null;
  return t ? n = !0 : e && !t && (n = !1), n;
}
const Mg = () => !0;
function a_(e, { connectionMode: t, connectionRadius: n, handleId: r, nodeId: o, edgeUpdaterType: i, isTarget: s, domNode: l, nodeLookup: u, lib: a, autoPanOnConnect: d, flowId: c, panBy: f, cancelConnection: m, onConnectStart: y, onConnect: w, onConnectEnd: x, isValidConnection: p = Mg, onReconnectEnd: g, updateConnection: h, getTransform: v, getFromHandle: S, autoPanSpeed: E, dragThreshold: M = 1, handleDomNode: P }) {
  const T = pg(e.target);
  let j = 0, I;
  const { x: D, y: H } = ct(e), k = Cg(i, P), A = l == null ? void 0 : l.getBoundingClientRect();
  let z = !1;
  if (!A || !k)
    return;
  const $ = Ng(o, k, r, u, t);
  if (!$)
    return;
  let N = ct(e, A), C = !1, L = null, O = !1, R = null;
  function W() {
    if (!d || !A)
      return;
    const [J, q] = cg(N, A, E);
    f({ x: J, y: q }), j = requestAnimationFrame(W);
  }
  const U = {
    ...$,
    nodeId: o,
    type: k,
    position: $.position
  }, X = u.get(o);
  let K = {
    inProgress: !0,
    isValid: null,
    from: Tn(X, U, b.Left, !0),
    fromHandle: U,
    fromPosition: U.position,
    fromNode: X,
    to: N,
    toHandle: null,
    toPosition: Mf[U.position],
    toNode: null,
    pointer: N
  };
  function B() {
    z = !0, h(K), y == null || y(e, { nodeId: o, handleId: r, handleType: k });
  }
  M === 0 && B();
  function G(J) {
    if (!z) {
      const { x: Pe, y: Ft } = ct(J), kt = Pe - D, fn = Ft - H;
      if (!(kt * kt + fn * fn > M * M))
        return;
      B();
    }
    if (!S() || !U) {
      ee(J);
      return;
    }
    const q = v();
    N = ct(J, A), I = l_(Fo(N, q, !1, [1, 1]), n, u, U), C || (W(), C = !0);
    const Z = Pg(J, {
      handle: I,
      connectionMode: t,
      fromNodeId: o,
      fromHandleId: r,
      fromType: s ? "target" : "source",
      isValidConnection: p,
      doc: T,
      lib: a,
      flowId: c,
      nodeLookup: u
    });
    R = Z.handleDomNode, L = Z.connection, O = u_(!!I, Z.isValid);
    const ie = u.get(o), ue = ie ? Tn(ie, U, b.Left, !0) : K.from, oe = {
      ...K,
      from: ue,
      isValid: O,
      to: Z.toHandle && O ? as({ x: Z.toHandle.x, y: Z.toHandle.y }, q) : N,
      toHandle: Z.toHandle,
      toPosition: O && Z.toHandle ? Z.toHandle.position : Mf[U.position],
      toNode: Z.toHandle ? u.get(Z.toHandle.nodeId) : null,
      pointer: N
    };
    h(oe), K = oe;
  }
  function ee(J) {
    if (!("touches" in J && J.touches.length > 0)) {
      if (z) {
        (I || R) && L && O && (w == null || w(L));
        const { inProgress: q, ...Z } = K, ie = {
          ...Z,
          toPosition: K.toHandle ? K.toPosition : null
        };
        x == null || x(J, ie), i && (g == null || g(J, ie));
      }
      m(), cancelAnimationFrame(j), C = !1, O = !1, L = null, R = null, T.removeEventListener("mousemove", G), T.removeEventListener("mouseup", ee), T.removeEventListener("touchmove", G), T.removeEventListener("touchend", ee);
    }
  }
  T.addEventListener("mousemove", G), T.addEventListener("mouseup", ee), T.addEventListener("touchmove", G), T.addEventListener("touchend", ee);
}
function Pg(e, { handle: t, connectionMode: n, fromNodeId: r, fromHandleId: o, fromType: i, doc: s, lib: l, flowId: u, isValidConnection: a = Mg, nodeLookup: d }) {
  const c = i === "target", f = t ? s.querySelector(`.${l}-flow__handle[data-id="${u}-${t == null ? void 0 : t.nodeId}-${t == null ? void 0 : t.id}-${t == null ? void 0 : t.type}"]`) : null, { x: m, y } = ct(e), w = s.elementFromPoint(m, y), x = w != null && w.classList.contains(`${l}-flow__handle`) ? w : f, p = {
    handleDomNode: x,
    isValid: !1,
    connection: null,
    toHandle: null
  };
  if (x) {
    const g = Cg(void 0, x), h = x.getAttribute("data-nodeid"), v = x.getAttribute("data-handleid"), S = x.classList.contains("connectable"), E = x.classList.contains("connectableend");
    if (!h || !g)
      return p;
    const M = {
      source: c ? h : r,
      sourceHandle: c ? v : o,
      target: c ? r : h,
      targetHandle: c ? o : v
    };
    p.connection = M;
    const T = S && E && (n === pr.Strict ? c && g === "source" || !c && g === "target" : h !== r || v !== o);
    p.isValid = T && a(M), p.toHandle = Ng(h, g, v, d, n, !0);
  }
  return p;
}
const Iu = {
  onPointerDown: a_,
  isValid: Pg
};
function c_({ domNode: e, panZoom: t, getTransform: n, getViewScale: r }) {
  const o = We(e);
  function i({ translateExtent: l, width: u, height: a, zoomStep: d = 1, pannable: c = !0, zoomable: f = !0, inversePan: m = !1 }) {
    const y = (h) => {
      if (h.sourceEvent.type !== "wheel" || !t)
        return;
      const v = n(), S = h.sourceEvent.ctrlKey && Mo() ? 10 : 1, E = -h.sourceEvent.deltaY * (h.sourceEvent.deltaMode === 1 ? 0.05 : h.sourceEvent.deltaMode ? 1 : 2e-3) * d, M = v[2] * Math.pow(2, E * S);
      t.scaleTo(M);
    };
    let w = [0, 0];
    const x = (h) => {
      (h.sourceEvent.type === "mousedown" || h.sourceEvent.type === "touchstart") && (w = [
        h.sourceEvent.clientX ?? h.sourceEvent.touches[0].clientX,
        h.sourceEvent.clientY ?? h.sourceEvent.touches[0].clientY
      ]);
    }, p = (h) => {
      const v = n();
      if (h.sourceEvent.type !== "mousemove" && h.sourceEvent.type !== "touchmove" || !t)
        return;
      const S = [
        h.sourceEvent.clientX ?? h.sourceEvent.touches[0].clientX,
        h.sourceEvent.clientY ?? h.sourceEvent.touches[0].clientY
      ], E = [S[0] - w[0], S[1] - w[1]];
      w = S;
      const M = r() * Math.max(v[2], Math.log(v[2])) * (m ? -1 : 1), P = {
        x: v[0] - E[0] * M,
        y: v[1] - E[1] * M
      }, T = [
        [0, 0],
        [u, a]
      ];
      t.setViewportConstrained({
        x: P.x,
        y: P.y,
        zoom: v[2]
      }, T, l);
    }, g = ng().on("start", x).on("zoom", c ? p : null).on("zoom.wheel", f ? y : null);
    o.call(g, {});
  }
  function s() {
    o.on("zoom", null);
  }
  return {
    update: i,
    destroy: s,
    pointer: st
  };
}
const $s = (e) => ({
  x: e.x,
  y: e.y,
  zoom: e.k
}), Sl = ({ x: e, y: t, zoom: n }) => Ts.translate(e, t).scale(n), Kn = (e, t) => e.target.closest(`.${t}`), zg = (e, t) => t === 2 && Array.isArray(e) && e.includes(2), f_ = (e) => ((e *= 2) <= 1 ? e * e * e : (e -= 2) * e * e + 2) / 2, _l = (e, t = 0, n = f_, r = () => {
}) => {
  const o = typeof t == "number" && t > 0;
  return o || r(), o ? e.transition().duration(t).ease(n).on("end", r) : e;
}, Ig = (e) => {
  const t = e.ctrlKey && Mo() ? 10 : 1;
  return -e.deltaY * (e.deltaMode === 1 ? 0.05 : e.deltaMode ? 1 : 2e-3) * t;
};
function d_({ zoomPanValues: e, noWheelClassName: t, d3Selection: n, d3Zoom: r, panOnScrollMode: o, panOnScrollSpeed: i, zoomOnPinch: s, onPanZoomStart: l, onPanZoom: u, onPanZoomEnd: a }) {
  return (d) => {
    if (Kn(d, t))
      return d.ctrlKey && d.preventDefault(), !1;
    d.preventDefault(), d.stopImmediatePropagation();
    const c = n.property("__zoom").k || 1;
    if (d.ctrlKey && s) {
      const x = st(d), p = Ig(d), g = c * Math.pow(2, p);
      r.scaleTo(n, g, x, d);
      return;
    }
    const f = d.deltaMode === 1 ? 20 : 1;
    let m = o === _n.Vertical ? 0 : d.deltaX * f, y = o === _n.Horizontal ? 0 : d.deltaY * f;
    !Mo() && d.shiftKey && o !== _n.Vertical && (m = d.deltaY * f, y = 0), r.translateBy(
      n,
      -(m / c) * i,
      -(y / c) * i,
      // @ts-ignore
      { internal: !0 }
    );
    const w = $s(n.property("__zoom"));
    clearTimeout(e.panScrollTimeout), e.isPanScrolling ? (u == null || u(d, w), e.panScrollTimeout = setTimeout(() => {
      a == null || a(d, w), e.isPanScrolling = !1;
    }, 150)) : (e.isPanScrolling = !0, l == null || l(d, w));
  };
}
function h_({ noWheelClassName: e, preventScrolling: t, d3ZoomHandler: n }) {
  return function(r, o) {
    const i = r.type === "wheel", s = !t && i && !r.ctrlKey, l = Kn(r, e);
    if (r.ctrlKey && i && l && r.preventDefault(), s || l)
      return null;
    r.preventDefault(), n.call(this, r, o);
  };
}
function p_({ zoomPanValues: e, onDraggingChange: t, onPanZoomStart: n }) {
  return (r) => {
    var i, s, l;
    if ((i = r.sourceEvent) != null && i.internal)
      return;
    const o = $s(r.transform);
    e.mouseButton = ((s = r.sourceEvent) == null ? void 0 : s.button) || 0, e.isZoomingOrPanning = !0, e.prevViewport = o, ((l = r.sourceEvent) == null ? void 0 : l.type) === "mousedown" && t(!0), n && (n == null || n(r.sourceEvent, o));
  };
}
function g_({ zoomPanValues: e, panOnDrag: t, onPaneContextMenu: n, onTransformChange: r, onPanZoom: o }) {
  return (i) => {
    var s, l;
    e.usedRightMouseButton = !!(n && zg(t, e.mouseButton ?? 0)), (s = i.sourceEvent) != null && s.sync || r([i.transform.x, i.transform.y, i.transform.k]), o && !((l = i.sourceEvent) != null && l.internal) && (o == null || o(i.sourceEvent, $s(i.transform)));
  };
}
function m_({ zoomPanValues: e, panOnDrag: t, panOnScroll: n, onDraggingChange: r, onPanZoomEnd: o, onPaneContextMenu: i }) {
  return (s) => {
    var l;
    if (!((l = s.sourceEvent) != null && l.internal) && (e.isZoomingOrPanning = !1, i && zg(t, e.mouseButton ?? 0) && !e.usedRightMouseButton && s.sourceEvent && i(s.sourceEvent), e.usedRightMouseButton = !1, r(!1), o)) {
      const u = $s(s.transform);
      e.prevViewport = u, clearTimeout(e.timerId), e.timerId = setTimeout(
        () => {
          o == null || o(s.sourceEvent, u);
        },
        // we need a setTimeout for panOnScroll to suppress multiple end events fired during scroll
        n ? 150 : 0
      );
    }
  };
}
function y_({ zoomActivationKeyPressed: e, zoomOnScroll: t, zoomOnPinch: n, panOnDrag: r, panOnScroll: o, zoomOnDoubleClick: i, userSelectionActive: s, noWheelClassName: l, noPanClassName: u, lib: a, connectionInProgress: d }) {
  return (c) => {
    var x;
    const f = e || t, m = n && c.ctrlKey, y = c.type === "wheel";
    if (c.button === 1 && c.type === "mousedown" && (Kn(c, `${a}-flow__node`) || Kn(c, `${a}-flow__edge`)))
      return !0;
    if (!r && !f && !o && !i && !n || s || d && !y || Kn(c, l) && y || Kn(c, u) && (!y || o && y && !e) || !n && c.ctrlKey && y)
      return !1;
    if (!n && c.type === "touchstart" && ((x = c.touches) == null ? void 0 : x.length) > 1)
      return c.preventDefault(), !1;
    if (!f && !o && !m && y || !r && (c.type === "mousedown" || c.type === "touchstart") || Array.isArray(r) && !r.includes(c.button) && c.type === "mousedown")
      return !1;
    const w = Array.isArray(r) && r.includes(c.button) || !c.button || c.button <= 1;
    return (!c.ctrlKey || y) && w;
  };
}
function v_({ domNode: e, minZoom: t, maxZoom: n, translateExtent: r, viewport: o, onPanZoom: i, onPanZoomStart: s, onPanZoomEnd: l, onDraggingChange: u }) {
  const a = {
    isZoomingOrPanning: !1,
    usedRightMouseButton: !1,
    prevViewport: {},
    mouseButton: 0,
    timerId: void 0,
    panScrollTimeout: void 0,
    isPanScrolling: !1
  }, d = e.getBoundingClientRect(), c = ng().scaleExtent([t, n]).translateExtent(r), f = We(e).call(c);
  g({
    x: o.x,
    y: o.y,
    zoom: gr(o.zoom, t, n)
  }, [
    [0, 0],
    [d.width, d.height]
  ], r);
  const m = f.on("wheel.zoom"), y = f.on("dblclick.zoom");
  c.wheelDelta(Ig);
  function w(I, D) {
    return f ? new Promise((H) => {
      c == null || c.interpolate((D == null ? void 0 : D.interpolate) === "linear" ? Jr : Mi).transform(_l(f, D == null ? void 0 : D.duration, D == null ? void 0 : D.ease, () => H(!0)), I);
    }) : Promise.resolve(!1);
  }
  function x({ noWheelClassName: I, noPanClassName: D, onPaneContextMenu: H, userSelectionActive: k, panOnScroll: A, panOnDrag: z, panOnScrollMode: $, panOnScrollSpeed: N, preventScrolling: C, zoomOnPinch: L, zoomOnScroll: O, zoomOnDoubleClick: R, zoomActivationKeyPressed: W, lib: U, onTransformChange: X, connectionInProgress: Q, paneClickDistance: K, selectionOnDrag: B }) {
    k && !a.isZoomingOrPanning && p();
    const G = A && !W && !k;
    c.clickDistance(B ? 1 / 0 : !at(K) || K < 0 ? 0 : K);
    const ee = G ? d_({
      zoomPanValues: a,
      noWheelClassName: I,
      d3Selection: f,
      d3Zoom: c,
      panOnScrollMode: $,
      panOnScrollSpeed: N,
      zoomOnPinch: L,
      onPanZoomStart: s,
      onPanZoom: i,
      onPanZoomEnd: l
    }) : h_({
      noWheelClassName: I,
      preventScrolling: C,
      d3ZoomHandler: m
    });
    if (f.on("wheel.zoom", ee, { passive: !1 }), !k) {
      const q = p_({
        zoomPanValues: a,
        onDraggingChange: u,
        onPanZoomStart: s
      });
      c.on("start", q);
      const Z = g_({
        zoomPanValues: a,
        panOnDrag: z,
        onPaneContextMenu: !!H,
        onPanZoom: i,
        onTransformChange: X
      });
      c.on("zoom", Z);
      const ie = m_({
        zoomPanValues: a,
        panOnDrag: z,
        panOnScroll: A,
        onPaneContextMenu: H,
        onPanZoomEnd: l,
        onDraggingChange: u
      });
      c.on("end", ie);
    }
    const J = y_({
      zoomActivationKeyPressed: W,
      panOnDrag: z,
      zoomOnScroll: O,
      panOnScroll: A,
      zoomOnDoubleClick: R,
      zoomOnPinch: L,
      userSelectionActive: k,
      noPanClassName: D,
      noWheelClassName: I,
      lib: U,
      connectionInProgress: Q
    });
    c.filter(J), R ? f.on("dblclick.zoom", y) : f.on("dblclick.zoom", null);
  }
  function p() {
    c.on("zoom", null);
  }
  async function g(I, D, H) {
    const k = Sl(I), A = c == null ? void 0 : c.constrain()(k, D, H);
    return A && await w(A), new Promise((z) => z(A));
  }
  async function h(I, D) {
    const H = Sl(I);
    return await w(H, D), new Promise((k) => k(H));
  }
  function v(I) {
    if (f) {
      const D = Sl(I), H = f.property("__zoom");
      (H.k !== I.zoom || H.x !== I.x || H.y !== I.y) && (c == null || c.transform(f, D, null, { sync: !0 }));
    }
  }
  function S() {
    const I = f ? tg(f.node()) : { x: 0, y: 0, k: 1 };
    return { x: I.x, y: I.y, zoom: I.k };
  }
  function E(I, D) {
    return f ? new Promise((H) => {
      c == null || c.interpolate((D == null ? void 0 : D.interpolate) === "linear" ? Jr : Mi).scaleTo(_l(f, D == null ? void 0 : D.duration, D == null ? void 0 : D.ease, () => H(!0)), I);
    }) : Promise.resolve(!1);
  }
  function M(I, D) {
    return f ? new Promise((H) => {
      c == null || c.interpolate((D == null ? void 0 : D.interpolate) === "linear" ? Jr : Mi).scaleBy(_l(f, D == null ? void 0 : D.duration, D == null ? void 0 : D.ease, () => H(!0)), I);
    }) : Promise.resolve(!1);
  }
  function P(I) {
    c == null || c.scaleExtent(I);
  }
  function T(I) {
    c == null || c.translateExtent(I);
  }
  function j(I) {
    const D = !at(I) || I < 0 ? 0 : I;
    c == null || c.clickDistance(D);
  }
  return {
    update: x,
    destroy: p,
    setViewport: h,
    setViewportConstrained: g,
    getViewport: S,
    scaleTo: E,
    scaleBy: M,
    setScaleExtent: P,
    setTranslateExtent: T,
    syncViewport: v,
    setClickDistance: j
  };
}
var vr;
(function(e) {
  e.Line = "line", e.Handle = "handle";
})(vr || (vr = {}));
function w_({ width: e, prevWidth: t, height: n, prevHeight: r, affectsX: o, affectsY: i }) {
  const s = e - t, l = n - r, u = [s > 0 ? 1 : s < 0 ? -1 : 0, l > 0 ? 1 : l < 0 ? -1 : 0];
  return s && o && (u[0] = u[0] * -1), l && i && (u[1] = u[1] * -1), u;
}
function Hf(e) {
  const t = e.includes("right") || e.includes("left"), n = e.includes("bottom") || e.includes("top"), r = e.includes("left"), o = e.includes("top");
  return {
    isHorizontal: t,
    isVertical: n,
    affectsX: r,
    affectsY: o
  };
}
function Vt(e, t) {
  return Math.max(0, t - e);
}
function Bt(e, t) {
  return Math.max(0, e - t);
}
function fi(e, t, n) {
  return Math.max(0, t - e, e - n);
}
function Vf(e, t) {
  return e ? !t : t;
}
function x_(e, t, n, r, o, i, s, l) {
  let { affectsX: u, affectsY: a } = t;
  const { isHorizontal: d, isVertical: c } = t, f = d && c, { xSnapped: m, ySnapped: y } = n, { minWidth: w, maxWidth: x, minHeight: p, maxHeight: g } = r, { x: h, y: v, width: S, height: E, aspectRatio: M } = e;
  let P = Math.floor(d ? m - e.pointerX : 0), T = Math.floor(c ? y - e.pointerY : 0);
  const j = S + (u ? -P : P), I = E + (a ? -T : T), D = -i[0] * S, H = -i[1] * E;
  let k = fi(j, w, x), A = fi(I, p, g);
  if (s) {
    let N = 0, C = 0;
    u && P < 0 ? N = Vt(h + P + D, s[0][0]) : !u && P > 0 && (N = Bt(h + j + D, s[1][0])), a && T < 0 ? C = Vt(v + T + H, s[0][1]) : !a && T > 0 && (C = Bt(v + I + H, s[1][1])), k = Math.max(k, N), A = Math.max(A, C);
  }
  if (l) {
    let N = 0, C = 0;
    u && P > 0 ? N = Bt(h + P, l[0][0]) : !u && P < 0 && (N = Vt(h + j, l[1][0])), a && T > 0 ? C = Bt(v + T, l[0][1]) : !a && T < 0 && (C = Vt(v + I, l[1][1])), k = Math.max(k, N), A = Math.max(A, C);
  }
  if (o) {
    if (d) {
      const N = fi(j / M, p, g) * M;
      if (k = Math.max(k, N), s) {
        let C = 0;
        !u && !a || u && !a && f ? C = Bt(v + H + j / M, s[1][1]) * M : C = Vt(v + H + (u ? P : -P) / M, s[0][1]) * M, k = Math.max(k, C);
      }
      if (l) {
        let C = 0;
        !u && !a || u && !a && f ? C = Vt(v + j / M, l[1][1]) * M : C = Bt(v + (u ? P : -P) / M, l[0][1]) * M, k = Math.max(k, C);
      }
    }
    if (c) {
      const N = fi(I * M, w, x) / M;
      if (A = Math.max(A, N), s) {
        let C = 0;
        !u && !a || a && !u && f ? C = Bt(h + I * M + D, s[1][0]) / M : C = Vt(h + (a ? T : -T) * M + D, s[0][0]) / M, A = Math.max(A, C);
      }
      if (l) {
        let C = 0;
        !u && !a || a && !u && f ? C = Vt(h + I * M, l[1][0]) / M : C = Bt(h + (a ? T : -T) * M, l[0][0]) / M, A = Math.max(A, C);
      }
    }
  }
  T = T + (T < 0 ? A : -A), P = P + (P < 0 ? k : -k), o && (f ? j > I * M ? T = (Vf(u, a) ? -P : P) / M : P = (Vf(u, a) ? -T : T) * M : d ? (T = P / M, a = u) : (P = T * M, u = a));
  const z = u ? h + P : h, $ = a ? v + T : v;
  return {
    width: S + (u ? -P : P),
    height: E + (a ? -T : T),
    x: i[0] * P * (u ? -1 : 1) + z,
    y: i[1] * T * (a ? -1 : 1) + $
  };
}
const Tg = { width: 0, height: 0, x: 0, y: 0 }, S_ = {
  ...Tg,
  pointerX: 0,
  pointerY: 0,
  aspectRatio: 1
};
function __(e) {
  return [
    [0, 0],
    [e.measured.width, e.measured.height]
  ];
}
function E_(e, t, n) {
  const r = t.position.x + e.position.x, o = t.position.y + e.position.y, i = e.measured.width ?? 0, s = e.measured.height ?? 0, l = n[0] * i, u = n[1] * s;
  return [
    [r - l, o - u],
    [r + i - l, o + s - u]
  ];
}
function k_({ domNode: e, nodeId: t, getStoreItems: n, onChange: r, onEnd: o }) {
  const i = We(e);
  let s = {
    controlDirection: Hf("bottom-right"),
    boundaries: {
      minWidth: 0,
      minHeight: 0,
      maxWidth: Number.MAX_VALUE,
      maxHeight: Number.MAX_VALUE
    },
    resizeDirection: void 0,
    keepAspectRatio: !1
  };
  function l({ controlPosition: a, boundaries: d, keepAspectRatio: c, resizeDirection: f, onResizeStart: m, onResize: y, onResizeEnd: w, shouldResize: x }) {
    let p = { ...Tg }, g = { ...S_ };
    s = {
      boundaries: d,
      resizeDirection: f,
      keepAspectRatio: c,
      controlDirection: Hf(a)
    };
    let h, v = null, S = [], E, M, P, T = !1;
    const j = Vp().on("start", (I) => {
      const { nodeLookup: D, transform: H, snapGrid: k, snapToGrid: A, nodeOrigin: z, paneDomNode: $ } = n();
      if (h = D.get(t), !h)
        return;
      v = ($ == null ? void 0 : $.getBoundingClientRect()) ?? null;
      const { xSnapped: N, ySnapped: C } = eo(I.sourceEvent, {
        transform: H,
        snapGrid: k,
        snapToGrid: A,
        containerBounds: v
      });
      p = {
        width: h.measured.width ?? 0,
        height: h.measured.height ?? 0,
        x: h.position.x ?? 0,
        y: h.position.y ?? 0
      }, g = {
        ...p,
        pointerX: N,
        pointerY: C,
        aspectRatio: p.width / p.height
      }, E = void 0, h.parentId && (h.extent === "parent" || h.expandParent) && (E = D.get(h.parentId), M = E && h.extent === "parent" ? __(E) : void 0), S = [], P = void 0;
      for (const [L, O] of D)
        if (O.parentId === t && (S.push({
          id: L,
          position: { ...O.position },
          extent: O.extent
        }), O.extent === "parent" || O.expandParent)) {
          const R = E_(O, h, O.origin ?? z);
          P ? P = [
            [Math.min(R[0][0], P[0][0]), Math.min(R[0][1], P[0][1])],
            [Math.max(R[1][0], P[1][0]), Math.max(R[1][1], P[1][1])]
          ] : P = R;
        }
      m == null || m(I, { ...p });
    }).on("drag", (I) => {
      const { transform: D, snapGrid: H, snapToGrid: k, nodeOrigin: A } = n(), z = eo(I.sourceEvent, {
        transform: D,
        snapGrid: H,
        snapToGrid: k,
        containerBounds: v
      }), $ = [];
      if (!h)
        return;
      const { x: N, y: C, width: L, height: O } = p, R = {}, W = h.origin ?? A, { width: U, height: X, x: Q, y: K } = x_(g, s.controlDirection, z, s.boundaries, s.keepAspectRatio, W, M, P), B = U !== L, G = X !== O, ee = Q !== N && B, J = K !== C && G;
      if (!ee && !J && !B && !G)
        return;
      if ((ee || J || W[0] === 1 || W[1] === 1) && (R.x = ee ? Q : p.x, R.y = J ? K : p.y, p.x = R.x, p.y = R.y, S.length > 0)) {
        const ue = Q - N, oe = K - C;
        for (const Pe of S)
          Pe.position = {
            x: Pe.position.x - ue + W[0] * (U - L),
            y: Pe.position.y - oe + W[1] * (X - O)
          }, $.push(Pe);
      }
      if ((B || G) && (R.width = B && (!s.resizeDirection || s.resizeDirection === "horizontal") ? U : p.width, R.height = G && (!s.resizeDirection || s.resizeDirection === "vertical") ? X : p.height, p.width = R.width, p.height = R.height), E && h.expandParent) {
        const ue = W[0] * (R.width ?? 0);
        R.x && R.x < ue && (p.x = ue, g.x = g.x - (R.x - ue));
        const oe = W[1] * (R.height ?? 0);
        R.y && R.y < oe && (p.y = oe, g.y = g.y - (R.y - oe));
      }
      const q = w_({
        width: p.width,
        prevWidth: L,
        height: p.height,
        prevHeight: O,
        affectsX: s.controlDirection.affectsX,
        affectsY: s.controlDirection.affectsY
      }), Z = { ...p, direction: q };
      (x == null ? void 0 : x(I, Z)) !== !1 && (T = !0, y == null || y(I, Z), r(R, $));
    }).on("end", (I) => {
      T && (w == null || w(I, { ...p }), o == null || o({ ...p }), T = !1);
    });
    i.call(j);
  }
  function u() {
    i.on(".drag", null);
  }
  return {
    update: l,
    destroy: u
  };
}
var Lg = { exports: {} }, Ag = {}, $g = { exports: {} }, Dg = {};
/**
 * @license React
 * use-sync-external-store-shim.production.js
 *
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var wr = F;
function N_(e, t) {
  return e === t && (e !== 0 || 1 / e === 1 / t) || e !== e && t !== t;
}
var C_ = typeof Object.is == "function" ? Object.is : N_, M_ = wr.useState, P_ = wr.useEffect, z_ = wr.useLayoutEffect, I_ = wr.useDebugValue;
function T_(e, t) {
  var n = t(), r = M_({ inst: { value: n, getSnapshot: t } }), o = r[0].inst, i = r[1];
  return z_(
    function() {
      o.value = n, o.getSnapshot = t, El(o) && i({ inst: o });
    },
    [e, n, t]
  ), P_(
    function() {
      return El(o) && i({ inst: o }), e(function() {
        El(o) && i({ inst: o });
      });
    },
    [e]
  ), I_(n), n;
}
function El(e) {
  var t = e.getSnapshot;
  e = e.value;
  try {
    var n = t();
    return !C_(e, n);
  } catch {
    return !0;
  }
}
function L_(e, t) {
  return t();
}
var A_ = typeof window > "u" || typeof window.document > "u" || typeof window.document.createElement > "u" ? L_ : T_;
Dg.useSyncExternalStore = wr.useSyncExternalStore !== void 0 ? wr.useSyncExternalStore : A_;
$g.exports = Dg;
var $_ = $g.exports;
/**
 * @license React
 * use-sync-external-store-shim/with-selector.production.js
 *
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var Ds = F, D_ = $_;
function R_(e, t) {
  return e === t && (e !== 0 || 1 / e === 1 / t) || e !== e && t !== t;
}
var j_ = typeof Object.is == "function" ? Object.is : R_, O_ = D_.useSyncExternalStore, F_ = Ds.useRef, H_ = Ds.useEffect, V_ = Ds.useMemo, B_ = Ds.useDebugValue;
Ag.useSyncExternalStoreWithSelector = function(e, t, n, r, o) {
  var i = F_(null);
  if (i.current === null) {
    var s = { hasValue: !1, value: null };
    i.current = s;
  } else s = i.current;
  i = V_(
    function() {
      function u(m) {
        if (!a) {
          if (a = !0, d = m, m = r(m), o !== void 0 && s.hasValue) {
            var y = s.value;
            if (o(y, m))
              return c = y;
          }
          return c = m;
        }
        if (y = c, j_(d, m)) return y;
        var w = r(m);
        return o !== void 0 && o(y, w) ? (d = m, y) : (d = m, c = w);
      }
      var a = !1, d, c, f = n === void 0 ? null : n;
      return [
        function() {
          return u(t());
        },
        f === null ? void 0 : function() {
          return u(f());
        }
      ];
    },
    [t, n, r, o]
  );
  var l = O_(e, i[0], i[1]);
  return H_(
    function() {
      s.hasValue = !0, s.value = l;
    },
    [l]
  ), B_(l), l;
};
Lg.exports = Ag;
var U_ = Lg.exports;
const W_ = /* @__PURE__ */ hd(U_), Y_ = {}, Bf = (e) => {
  let t;
  const n = /* @__PURE__ */ new Set(), r = (d, c) => {
    const f = typeof d == "function" ? d(t) : d;
    if (!Object.is(f, t)) {
      const m = t;
      t = c ?? (typeof f != "object" || f === null) ? f : Object.assign({}, t, f), n.forEach((y) => y(t, m));
    }
  }, o = () => t, u = { setState: r, getState: o, getInitialState: () => a, subscribe: (d) => (n.add(d), () => n.delete(d)), destroy: () => {
    (Y_ ? "production" : void 0) !== "production" && console.warn(
      "[DEPRECATED] The `destroy` method will be unsupported in a future version. Instead use unsubscribe function returned by subscribe. Everything will be garbage-collected if store is garbage-collected."
    ), n.clear();
  } }, a = t = e(r, o, u);
  return u;
}, X_ = (e) => e ? Bf(e) : Bf, { useDebugValue: Q_ } = D0, { useSyncExternalStoreWithSelector: b_ } = W_, K_ = (e) => e;
function Rg(e, t = K_, n) {
  const r = b_(
    e.subscribe,
    e.getState,
    e.getServerState || e.getInitialState,
    t,
    n
  );
  return Q_(r), r;
}
const Uf = (e, t) => {
  const n = X_(e), r = (o, i = t) => Rg(n, o, i);
  return Object.assign(r, n), r;
}, G_ = (e, t) => e ? Uf(e, t) : Uf;
function de(e, t) {
  if (Object.is(e, t))
    return !0;
  if (typeof e != "object" || e === null || typeof t != "object" || t === null)
    return !1;
  if (e instanceof Map && t instanceof Map) {
    if (e.size !== t.size) return !1;
    for (const [r, o] of e)
      if (!Object.is(o, t.get(r)))
        return !1;
    return !0;
  }
  if (e instanceof Set && t instanceof Set) {
    if (e.size !== t.size) return !1;
    for (const r of e)
      if (!t.has(r))
        return !1;
    return !0;
  }
  const n = Object.keys(e);
  if (n.length !== Object.keys(t).length)
    return !1;
  for (const r of n)
    if (!Object.prototype.hasOwnProperty.call(t, r) || !Object.is(e[r], t[r]))
      return !1;
  return !0;
}
const Rs = F.createContext(null), Z_ = Rs.Provider, jg = _t.error001();
function ne(e, t) {
  const n = F.useContext(Rs);
  if (n === null)
    throw new Error(jg);
  return Rg(n, e, t);
}
function he() {
  const e = F.useContext(Rs);
  if (e === null)
    throw new Error(jg);
  return F.useMemo(() => ({
    getState: e.getState,
    setState: e.setState,
    subscribe: e.subscribe
  }), [e]);
}
const Wf = { display: "none" }, q_ = {
  position: "absolute",
  width: 1,
  height: 1,
  margin: -1,
  border: 0,
  padding: 0,
  overflow: "hidden",
  clip: "rect(0px, 0px, 0px, 0px)",
  clipPath: "inset(100%)"
}, Og = "react-flow__node-desc", Fg = "react-flow__edge-desc", J_ = "react-flow__aria-live", eE = (e) => e.ariaLiveMessage, tE = (e) => e.ariaLabelConfig;
function nE({ rfId: e }) {
  const t = ne(eE);
  return _.jsx("div", { id: `${J_}-${e}`, "aria-live": "assertive", "aria-atomic": "true", style: q_, children: t });
}
function rE({ rfId: e, disableKeyboardA11y: t }) {
  const n = ne(tE);
  return _.jsxs(_.Fragment, { children: [_.jsx("div", { id: `${Og}-${e}`, style: Wf, children: t ? n["node.a11yDescription.default"] : n["node.a11yDescription.keyboardDisabled"] }), _.jsx("div", { id: `${Fg}-${e}`, style: Wf, children: n["edge.a11yDescription.default"] }), !t && _.jsx(nE, { rfId: e })] });
}
const js = F.forwardRef(({ position: e = "top-left", children: t, className: n, style: r, ...o }, i) => {
  const s = `${e}`.split("-");
  return _.jsx("div", { className: xe(["react-flow__panel", n, ...s]), style: r, ref: i, ...o, children: t });
});
js.displayName = "Panel";
function oE({ proOptions: e, position: t = "bottom-right" }) {
  return e != null && e.hideAttribution ? null : _.jsx(js, { position: t, className: "react-flow__attribution", "data-message": "Please only hide this attribution when you are subscribed to React Flow Pro: https://pro.reactflow.dev", children: _.jsx("a", { href: "https://reactflow.dev", target: "_blank", rel: "noopener noreferrer", "aria-label": "React Flow attribution", children: "React Flow" }) });
}
const iE = (e) => {
  const t = [], n = [];
  for (const [, r] of e.nodeLookup)
    r.selected && t.push(r.internals.userNode);
  for (const [, r] of e.edgeLookup)
    r.selected && n.push(r);
  return { selectedNodes: t, selectedEdges: n };
}, di = (e) => e.id;
function sE(e, t) {
  return de(e.selectedNodes.map(di), t.selectedNodes.map(di)) && de(e.selectedEdges.map(di), t.selectedEdges.map(di));
}
function lE({ onSelectionChange: e }) {
  const t = he(), { selectedNodes: n, selectedEdges: r } = ne(iE, sE);
  return F.useEffect(() => {
    const o = { nodes: n, edges: r };
    e == null || e(o), t.getState().onSelectionChangeHandlers.forEach((i) => i(o));
  }, [n, r, e]), null;
}
const uE = (e) => !!e.onSelectionChangeHandlers;
function aE({ onSelectionChange: e }) {
  const t = ne(uE);
  return e || t ? _.jsx(lE, { onSelectionChange: e }) : null;
}
const Tu = typeof window < "u" ? F.useLayoutEffect : F.useEffect, Hg = [0, 0], cE = { x: 0, y: 0, zoom: 1 }, fE = [
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
], Yf = [...fE, "rfId"], dE = (e) => ({
  setNodes: e.setNodes,
  setEdges: e.setEdges,
  setMinZoom: e.setMinZoom,
  setMaxZoom: e.setMaxZoom,
  setTranslateExtent: e.setTranslateExtent,
  setNodeExtent: e.setNodeExtent,
  reset: e.reset,
  setDefaultNodesAndEdges: e.setDefaultNodesAndEdges
}), Xf = {
  /*
   * these are values that are also passed directly to other components
   * than the StoreUpdater. We can reduce the number of setStore calls
   * by setting the same values here as prev fields.
   */
  translateExtent: Eo,
  nodeOrigin: Hg,
  minZoom: 0.5,
  maxZoom: 2,
  elementsSelectable: !0,
  noPanClassName: "nopan",
  rfId: "1"
};
function hE(e) {
  const { setNodes: t, setEdges: n, setMinZoom: r, setMaxZoom: o, setTranslateExtent: i, setNodeExtent: s, reset: l, setDefaultNodesAndEdges: u } = ne(dE, de), a = he();
  Tu(() => (u(e.defaultNodes, e.defaultEdges), () => {
    d.current = Xf, l();
  }), []);
  const d = F.useRef(Xf);
  return Tu(
    () => {
      for (const c of Yf) {
        const f = e[c], m = d.current[c];
        f !== m && (typeof e[c] > "u" || (c === "nodes" ? t(f) : c === "edges" ? n(f) : c === "minZoom" ? r(f) : c === "maxZoom" ? o(f) : c === "translateExtent" ? i(f) : c === "nodeExtent" ? s(f) : c === "ariaLabelConfig" ? a.setState({ ariaLabelConfig: RS(f) }) : c === "fitView" ? a.setState({ fitViewQueued: f }) : c === "fitViewOptions" ? a.setState({ fitViewOptions: f }) : a.setState({ [c]: f })));
      }
      d.current = e;
    },
    // Only re-run the effect if one of the fields we track changes
    Yf.map((c) => e[c])
  ), null;
}
function Qf() {
  return typeof window > "u" || !window.matchMedia ? null : window.matchMedia("(prefers-color-scheme: dark)");
}
function pE(e) {
  var r;
  const [t, n] = F.useState(e === "system" ? null : e);
  return F.useEffect(() => {
    if (e !== "system") {
      n(e);
      return;
    }
    const o = Qf(), i = () => n(o != null && o.matches ? "dark" : "light");
    return i(), o == null || o.addEventListener("change", i), () => {
      o == null || o.removeEventListener("change", i);
    };
  }, [e]), t !== null ? t : (r = Qf()) != null && r.matches ? "dark" : "light";
}
const bf = typeof document < "u" ? document : null;
function Po(e = null, t = { target: bf, actInsideInputWithModifier: !0 }) {
  const [n, r] = F.useState(!1), o = F.useRef(!1), i = F.useRef(/* @__PURE__ */ new Set([])), [s, l] = F.useMemo(() => {
    if (e !== null) {
      const a = (Array.isArray(e) ? e : [e]).filter((c) => typeof c == "string").map((c) => c.replace("+", `
`).replace(`

`, `
+`).split(`
`)), d = a.reduce((c, f) => c.concat(...f), []);
      return [a, d];
    }
    return [[], []];
  }, [e]);
  return F.useEffect(() => {
    const u = (t == null ? void 0 : t.target) ?? bf, a = (t == null ? void 0 : t.actInsideInputWithModifier) ?? !0;
    if (e !== null) {
      const d = (m) => {
        var x, p;
        if (o.current = m.ctrlKey || m.metaKey || m.shiftKey || m.altKey, (!o.current || o.current && !a) && gg(m))
          return !1;
        const w = Gf(m.code, l);
        if (i.current.add(m[w]), Kf(s, i.current, !1)) {
          const g = ((p = (x = m.composedPath) == null ? void 0 : x.call(m)) == null ? void 0 : p[0]) || m.target, h = (g == null ? void 0 : g.nodeName) === "BUTTON" || (g == null ? void 0 : g.nodeName) === "A";
          t.preventDefault !== !1 && (o.current || !h) && m.preventDefault(), r(!0);
        }
      }, c = (m) => {
        const y = Gf(m.code, l);
        Kf(s, i.current, !0) ? (r(!1), i.current.clear()) : i.current.delete(m[y]), m.key === "Meta" && i.current.clear(), o.current = !1;
      }, f = () => {
        i.current.clear(), r(!1);
      };
      return u == null || u.addEventListener("keydown", d), u == null || u.addEventListener("keyup", c), window.addEventListener("blur", f), window.addEventListener("contextmenu", f), () => {
        u == null || u.removeEventListener("keydown", d), u == null || u.removeEventListener("keyup", c), window.removeEventListener("blur", f), window.removeEventListener("contextmenu", f);
      };
    }
  }, [e, r]), n;
}
function Kf(e, t, n) {
  return e.filter((r) => n || r.length === t.size).some((r) => r.every((o) => t.has(o)));
}
function Gf(e, t) {
  return t.includes(e) ? "code" : "key";
}
const gE = () => {
  const e = he();
  return F.useMemo(() => ({
    zoomIn: (t) => {
      const { panZoom: n } = e.getState();
      return n ? n.scaleBy(1.2, t) : Promise.resolve(!1);
    },
    zoomOut: (t) => {
      const { panZoom: n } = e.getState();
      return n ? n.scaleBy(1 / 1.2, t) : Promise.resolve(!1);
    },
    zoomTo: (t, n) => {
      const { panZoom: r } = e.getState();
      return r ? r.scaleTo(t, n) : Promise.resolve(!1);
    },
    getZoom: () => e.getState().transform[2],
    setViewport: async (t, n) => {
      const { transform: [r, o, i], panZoom: s } = e.getState();
      return s ? (await s.setViewport({
        x: t.x ?? r,
        y: t.y ?? o,
        zoom: t.zoom ?? i
      }, n), Promise.resolve(!0)) : Promise.resolve(!1);
    },
    getViewport: () => {
      const [t, n, r] = e.getState().transform;
      return { x: t, y: n, zoom: r };
    },
    setCenter: async (t, n, r) => e.getState().setCenter(t, n, r),
    fitBounds: async (t, n) => {
      const { width: r, height: o, minZoom: i, maxZoom: s, panZoom: l } = e.getState(), u = ja(t, r, o, i, s, (n == null ? void 0 : n.padding) ?? 0.1);
      return l ? (await l.setViewport(u, {
        duration: n == null ? void 0 : n.duration,
        ease: n == null ? void 0 : n.ease,
        interpolate: n == null ? void 0 : n.interpolate
      }), Promise.resolve(!0)) : Promise.resolve(!1);
    },
    screenToFlowPosition: (t, n = {}) => {
      const { transform: r, snapGrid: o, snapToGrid: i, domNode: s } = e.getState();
      if (!s)
        return t;
      const { x: l, y: u } = s.getBoundingClientRect(), a = {
        x: t.x - l,
        y: t.y - u
      }, d = n.snapGrid ?? o, c = n.snapToGrid ?? i;
      return Fo(a, r, c, d);
    },
    flowToScreenPosition: (t) => {
      const { transform: n, domNode: r } = e.getState();
      if (!r)
        return t;
      const { x: o, y: i } = r.getBoundingClientRect(), s = as(t, n);
      return {
        x: s.x + o,
        y: s.y + i
      };
    }
  }), []);
};
function Vg(e, t) {
  const n = [], r = /* @__PURE__ */ new Map(), o = [];
  for (const i of e)
    if (i.type === "add") {
      o.push(i);
      continue;
    } else if (i.type === "remove" || i.type === "replace")
      r.set(i.id, [i]);
    else {
      const s = r.get(i.id);
      s ? s.push(i) : r.set(i.id, [i]);
    }
  for (const i of t) {
    const s = r.get(i.id);
    if (!s) {
      n.push(i);
      continue;
    }
    if (s[0].type === "remove")
      continue;
    if (s[0].type === "replace") {
      n.push({ ...s[0].item });
      continue;
    }
    const l = { ...i };
    for (const u of s)
      mE(u, l);
    n.push(l);
  }
  return o.length && o.forEach((i) => {
    i.index !== void 0 ? n.splice(i.index, 0, { ...i.item }) : n.push({ ...i.item });
  }), n;
}
function mE(e, t) {
  switch (e.type) {
    case "select": {
      t.selected = e.selected;
      break;
    }
    case "position": {
      typeof e.position < "u" && (t.position = e.position), typeof e.dragging < "u" && (t.dragging = e.dragging);
      break;
    }
    case "dimensions": {
      typeof e.dimensions < "u" && (t.measured = {
        ...e.dimensions
      }, e.setAttributes && ((e.setAttributes === !0 || e.setAttributes === "width") && (t.width = e.dimensions.width), (e.setAttributes === !0 || e.setAttributes === "height") && (t.height = e.dimensions.height))), typeof e.resizing == "boolean" && (t.resizing = e.resizing);
      break;
    }
  }
}
function yE(e, t) {
  return Vg(e, t);
}
function vE(e, t) {
  return Vg(e, t);
}
function pn(e, t) {
  return {
    id: e,
    type: "select",
    selected: t
  };
}
function Gn(e, t = /* @__PURE__ */ new Set(), n = !1) {
  const r = [];
  for (const [o, i] of e) {
    const s = t.has(o);
    !(i.selected === void 0 && !s) && i.selected !== s && (n && (i.selected = s), r.push(pn(i.id, s)));
  }
  return r;
}
function Zf({ items: e = [], lookup: t }) {
  var o;
  const n = [], r = new Map(e.map((i) => [i.id, i]));
  for (const [i, s] of e.entries()) {
    const l = t.get(s.id), u = ((o = l == null ? void 0 : l.internals) == null ? void 0 : o.userNode) ?? l;
    u !== void 0 && u !== s && n.push({ id: s.id, item: s, type: "replace" }), u === void 0 && n.push({ item: s, type: "add", index: i });
  }
  for (const [i] of t)
    r.get(i) === void 0 && n.push({ id: i, type: "remove" });
  return n;
}
function qf(e) {
  return {
    id: e.id,
    type: "remove"
  };
}
const Jf = (e) => CS(e), wE = (e) => lg(e);
function Bg(e) {
  return F.forwardRef(e);
}
function ed(e) {
  const [t, n] = F.useState(BigInt(0)), [r] = F.useState(() => xE(() => n((o) => o + BigInt(1))));
  return Tu(() => {
    const o = r.get();
    o.length && (e(o), r.reset());
  }, [t]), r;
}
function xE(e) {
  let t = [];
  return {
    get: () => t,
    reset: () => {
      t = [];
    },
    push: (n) => {
      t.push(n), e();
    }
  };
}
const Ug = F.createContext(null);
function SE({ children: e }) {
  const t = he(), n = F.useCallback((l) => {
    const { nodes: u = [], setNodes: a, hasDefaultNodes: d, onNodesChange: c, nodeLookup: f, fitViewQueued: m, onNodesChangeMiddlewareMap: y } = t.getState();
    let w = u;
    for (const p of l)
      w = typeof p == "function" ? p(w) : p;
    let x = Zf({
      items: w,
      lookup: f
    });
    for (const p of y.values())
      x = p(x);
    d && a(w), x.length > 0 ? c == null || c(x) : m && window.requestAnimationFrame(() => {
      const { fitViewQueued: p, nodes: g, setNodes: h } = t.getState();
      p && h(g);
    });
  }, []), r = ed(n), o = F.useCallback((l) => {
    const { edges: u = [], setEdges: a, hasDefaultEdges: d, onEdgesChange: c, edgeLookup: f } = t.getState();
    let m = u;
    for (const y of l)
      m = typeof y == "function" ? y(m) : y;
    d ? a(m) : c && c(Zf({
      items: m,
      lookup: f
    }));
  }, []), i = ed(o), s = F.useMemo(() => ({ nodeQueue: r, edgeQueue: i }), []);
  return _.jsx(Ug.Provider, { value: s, children: e });
}
function _E() {
  const e = F.useContext(Ug);
  if (!e)
    throw new Error("useBatchContext must be used within a BatchProvider");
  return e;
}
const EE = (e) => !!e.panZoom;
function Os() {
  const e = gE(), t = he(), n = _E(), r = ne(EE), o = F.useMemo(() => {
    const i = (c) => t.getState().nodeLookup.get(c), s = (c) => {
      n.nodeQueue.push(c);
    }, l = (c) => {
      n.edgeQueue.push(c);
    }, u = (c) => {
      var p, g;
      const { nodeLookup: f, nodeOrigin: m } = t.getState(), y = Jf(c) ? c : f.get(c.id), w = y.parentId ? hg(y.position, y.measured, y.parentId, f, m) : y.position, x = {
        ...y,
        position: w,
        width: ((p = y.measured) == null ? void 0 : p.width) ?? y.width,
        height: ((g = y.measured) == null ? void 0 : g.height) ?? y.height
      };
      return mr(x);
    }, a = (c, f, m = { replace: !1 }) => {
      s((y) => y.map((w) => {
        if (w.id === c) {
          const x = typeof f == "function" ? f(w) : f;
          return m.replace && Jf(x) ? x : { ...w, ...x };
        }
        return w;
      }));
    }, d = (c, f, m = { replace: !1 }) => {
      l((y) => y.map((w) => {
        if (w.id === c) {
          const x = typeof f == "function" ? f(w) : f;
          return m.replace && wE(x) ? x : { ...w, ...x };
        }
        return w;
      }));
    };
    return {
      getNodes: () => t.getState().nodes.map((c) => ({ ...c })),
      getNode: (c) => {
        var f;
        return (f = i(c)) == null ? void 0 : f.internals.userNode;
      },
      getInternalNode: i,
      getEdges: () => {
        const { edges: c = [] } = t.getState();
        return c.map((f) => ({ ...f }));
      },
      getEdge: (c) => t.getState().edgeLookup.get(c),
      setNodes: s,
      setEdges: l,
      addNodes: (c) => {
        const f = Array.isArray(c) ? c : [c];
        n.nodeQueue.push((m) => [...m, ...f]);
      },
      addEdges: (c) => {
        const f = Array.isArray(c) ? c : [c];
        n.edgeQueue.push((m) => [...m, ...f]);
      },
      toObject: () => {
        const { nodes: c = [], edges: f = [], transform: m } = t.getState(), [y, w, x] = m;
        return {
          nodes: c.map((p) => ({ ...p })),
          edges: f.map((p) => ({ ...p })),
          viewport: {
            x: y,
            y: w,
            zoom: x
          }
        };
      },
      deleteElements: async ({ nodes: c = [], edges: f = [] }) => {
        const { nodes: m, edges: y, onNodesDelete: w, onEdgesDelete: x, triggerNodeChanges: p, triggerEdgeChanges: g, onDelete: h, onBeforeDelete: v } = t.getState(), { nodes: S, edges: E } = await TS({
          nodesToRemove: c,
          edgesToRemove: f,
          nodes: m,
          edges: y,
          onBeforeDelete: v
        }), M = E.length > 0, P = S.length > 0;
        if (M) {
          const T = E.map(qf);
          x == null || x(E), g(T);
        }
        if (P) {
          const T = S.map(qf);
          w == null || w(S), p(T);
        }
        return (P || M) && (h == null || h({ nodes: S, edges: E })), { deletedNodes: S, deletedEdges: E };
      },
      /**
       * Partial is defined as "the 2 nodes/areas are intersecting partially".
       * If a is contained in b or b is contained in a, they are both
       * considered fully intersecting.
       */
      getIntersectingNodes: (c, f = !0, m) => {
        const y = zf(c), w = y ? c : u(c), x = m !== void 0;
        return w ? (m || t.getState().nodes).filter((p) => {
          const g = t.getState().nodeLookup.get(p.id);
          if (g && !y && (p.id === c.id || !g.internals.positionAbsolute))
            return !1;
          const h = mr(x ? p : g), v = Co(h, w);
          return f && v > 0 || v >= h.width * h.height || v >= w.width * w.height;
        }) : [];
      },
      isNodeIntersecting: (c, f, m = !0) => {
        const w = zf(c) ? c : u(c);
        if (!w)
          return !1;
        const x = Co(w, f);
        return m && x > 0 || x >= f.width * f.height || x >= w.width * w.height;
      },
      updateNode: a,
      updateNodeData: (c, f, m = { replace: !1 }) => {
        a(c, (y) => {
          const w = typeof f == "function" ? f(y) : f;
          return m.replace ? { ...y, data: w } : { ...y, data: { ...y.data, ...w } };
        }, m);
      },
      updateEdge: d,
      updateEdgeData: (c, f, m = { replace: !1 }) => {
        d(c, (y) => {
          const w = typeof f == "function" ? f(y) : f;
          return m.replace ? { ...y, data: w } : { ...y, data: { ...y.data, ...w } };
        }, m);
      },
      getNodesBounds: (c) => {
        const { nodeLookup: f, nodeOrigin: m } = t.getState();
        return MS(c, { nodeLookup: f, nodeOrigin: m });
      },
      getHandleConnections: ({ type: c, id: f, nodeId: m }) => {
        var y;
        return Array.from(((y = t.getState().connectionLookup.get(`${m}-${c}${f ? `-${f}` : ""}`)) == null ? void 0 : y.values()) ?? []);
      },
      getNodeConnections: ({ type: c, handleId: f, nodeId: m }) => {
        var y;
        return Array.from(((y = t.getState().connectionLookup.get(`${m}${c ? f ? `-${c}-${f}` : `-${c}` : ""}`)) == null ? void 0 : y.values()) ?? []);
      },
      fitView: async (c) => {
        const f = t.getState().fitViewResolver ?? DS();
        return t.setState({ fitViewQueued: !0, fitViewOptions: c, fitViewResolver: f }), n.nodeQueue.push((m) => [...m]), f.promise;
      }
    };
  }, []);
  return F.useMemo(() => ({
    ...o,
    ...e,
    viewportInitialized: r
  }), [r]);
}
const td = (e) => e.selected, kE = typeof window < "u" ? window : void 0;
function NE({ deleteKeyCode: e, multiSelectionKeyCode: t }) {
  const n = he(), { deleteElements: r } = Os(), o = Po(e, { actInsideInputWithModifier: !1 }), i = Po(t, { target: kE });
  F.useEffect(() => {
    if (o) {
      const { edges: s, nodes: l } = n.getState();
      r({ nodes: l.filter(td), edges: s.filter(td) }), n.setState({ nodesSelectionActive: !1 });
    }
  }, [o]), F.useEffect(() => {
    n.setState({ multiSelectionActive: i });
  }, [i]);
}
function CE(e) {
  const t = he();
  F.useEffect(() => {
    const n = () => {
      var o, i, s, l;
      if (!e.current || !(((i = (o = e.current).checkVisibility) == null ? void 0 : i.call(o)) ?? !0))
        return !1;
      const r = Oa(e.current);
      (r.height === 0 || r.width === 0) && ((l = (s = t.getState()).onError) == null || l.call(s, "004", _t.error004())), t.setState({ width: r.width || 500, height: r.height || 500 });
    };
    if (e.current) {
      n(), window.addEventListener("resize", n);
      const r = new ResizeObserver(() => n());
      return r.observe(e.current), () => {
        window.removeEventListener("resize", n), r && e.current && r.unobserve(e.current);
      };
    }
  }, []);
}
const Fs = {
  position: "absolute",
  width: "100%",
  height: "100%",
  top: 0,
  left: 0
}, ME = (e) => ({
  userSelectionActive: e.userSelectionActive,
  lib: e.lib,
  connectionInProgress: e.connection.inProgress
});
function PE({ onPaneContextMenu: e, zoomOnScroll: t = !0, zoomOnPinch: n = !0, panOnScroll: r = !1, panOnScrollSpeed: o = 0.5, panOnScrollMode: i = _n.Free, zoomOnDoubleClick: s = !0, panOnDrag: l = !0, defaultViewport: u, translateExtent: a, minZoom: d, maxZoom: c, zoomActivationKeyCode: f, preventScrolling: m = !0, children: y, noWheelClassName: w, noPanClassName: x, onViewportChange: p, isControlledViewport: g, paneClickDistance: h, selectionOnDrag: v }) {
  const S = he(), E = F.useRef(null), { userSelectionActive: M, lib: P, connectionInProgress: T } = ne(ME, de), j = Po(f), I = F.useRef();
  CE(E);
  const D = F.useCallback((H) => {
    p == null || p({ x: H[0], y: H[1], zoom: H[2] }), g || S.setState({ transform: H });
  }, [p, g]);
  return F.useEffect(() => {
    if (E.current) {
      I.current = v_({
        domNode: E.current,
        minZoom: d,
        maxZoom: c,
        translateExtent: a,
        viewport: u,
        onDraggingChange: (z) => S.setState(($) => $.paneDragging === z ? $ : { paneDragging: z }),
        onPanZoomStart: (z, $) => {
          const { onViewportChangeStart: N, onMoveStart: C } = S.getState();
          C == null || C(z, $), N == null || N($);
        },
        onPanZoom: (z, $) => {
          const { onViewportChange: N, onMove: C } = S.getState();
          C == null || C(z, $), N == null || N($);
        },
        onPanZoomEnd: (z, $) => {
          const { onViewportChangeEnd: N, onMoveEnd: C } = S.getState();
          C == null || C(z, $), N == null || N($);
        }
      });
      const { x: H, y: k, zoom: A } = I.current.getViewport();
      return S.setState({
        panZoom: I.current,
        transform: [H, k, A],
        domNode: E.current.closest(".react-flow")
      }), () => {
        var z;
        (z = I.current) == null || z.destroy();
      };
    }
  }, []), F.useEffect(() => {
    var H;
    (H = I.current) == null || H.update({
      onPaneContextMenu: e,
      zoomOnScroll: t,
      zoomOnPinch: n,
      panOnScroll: r,
      panOnScrollSpeed: o,
      panOnScrollMode: i,
      zoomOnDoubleClick: s,
      panOnDrag: l,
      zoomActivationKeyPressed: j,
      preventScrolling: m,
      noPanClassName: x,
      userSelectionActive: M,
      noWheelClassName: w,
      lib: P,
      onTransformChange: D,
      connectionInProgress: T,
      selectionOnDrag: v,
      paneClickDistance: h
    });
  }, [
    e,
    t,
    n,
    r,
    o,
    i,
    s,
    l,
    j,
    m,
    x,
    M,
    w,
    P,
    D,
    T,
    v,
    h
  ]), _.jsx("div", { className: "react-flow__renderer", ref: E, style: Fs, children: y });
}
const zE = (e) => ({
  userSelectionActive: e.userSelectionActive,
  userSelectionRect: e.userSelectionRect
});
function IE() {
  const { userSelectionActive: e, userSelectionRect: t } = ne(zE, de);
  return e && t ? _.jsx("div", { className: "react-flow__selection react-flow__container", style: {
    width: t.width,
    height: t.height,
    transform: `translate(${t.x}px, ${t.y}px)`
  } }) : null;
}
const kl = (e, t) => (n) => {
  n.target === t.current && (e == null || e(n));
}, TE = (e) => ({
  userSelectionActive: e.userSelectionActive,
  elementsSelectable: e.elementsSelectable,
  connectionInProgress: e.connection.inProgress,
  dragging: e.paneDragging
});
function LE({ isSelecting: e, selectionKeyPressed: t, selectionMode: n = ko.Full, panOnDrag: r, paneClickDistance: o, selectionOnDrag: i, onSelectionStart: s, onSelectionEnd: l, onPaneClick: u, onPaneContextMenu: a, onPaneScroll: d, onPaneMouseEnter: c, onPaneMouseMove: f, onPaneMouseLeave: m, children: y }) {
  const w = he(), { userSelectionActive: x, elementsSelectable: p, dragging: g, connectionInProgress: h } = ne(TE, de), v = p && (e || x), S = F.useRef(null), E = F.useRef(), M = F.useRef(/* @__PURE__ */ new Set()), P = F.useRef(/* @__PURE__ */ new Set()), T = F.useRef(!1), j = (N) => {
    if (T.current || h) {
      T.current = !1;
      return;
    }
    u == null || u(N), w.getState().resetSelectedElements(), w.setState({ nodesSelectionActive: !1 });
  }, I = (N) => {
    if (Array.isArray(r) && (r != null && r.includes(2))) {
      N.preventDefault();
      return;
    }
    a == null || a(N);
  }, D = d ? (N) => d(N) : void 0, H = (N) => {
    T.current && (N.stopPropagation(), T.current = !1);
  }, k = (N) => {
    var X, Q;
    const { domNode: C } = w.getState();
    if (E.current = C == null ? void 0 : C.getBoundingClientRect(), !E.current)
      return;
    const L = N.target === S.current;
    if (!L && !!N.target.closest(".nokey") || !e || !(i && L || t) || N.button !== 0 || !N.isPrimary)
      return;
    (Q = (X = N.target) == null ? void 0 : X.setPointerCapture) == null || Q.call(X, N.pointerId), T.current = !1;
    const { x: W, y: U } = ct(N.nativeEvent, E.current);
    w.setState({
      userSelectionRect: {
        width: 0,
        height: 0,
        startX: W,
        startY: U,
        x: W,
        y: U
      }
    }), L || (N.stopPropagation(), N.preventDefault());
  }, A = (N) => {
    const { userSelectionRect: C, transform: L, nodeLookup: O, edgeLookup: R, connectionLookup: W, triggerNodeChanges: U, triggerEdgeChanges: X, defaultEdgeOptions: Q, resetSelectedElements: K } = w.getState();
    if (!E.current || !C)
      return;
    const { x: B, y: G } = ct(N.nativeEvent, E.current), { startX: ee, startY: J } = C;
    if (!T.current) {
      const oe = t ? 0 : o;
      if (Math.hypot(B - ee, G - J) <= oe)
        return;
      K(), s == null || s(N);
    }
    T.current = !0;
    const q = {
      startX: ee,
      startY: J,
      x: B < ee ? B : ee,
      y: G < J ? G : J,
      width: Math.abs(B - ee),
      height: Math.abs(G - J)
    }, Z = M.current, ie = P.current;
    M.current = new Set(Ra(O, q, L, n === ko.Partial, !0).map((oe) => oe.id)), P.current = /* @__PURE__ */ new Set();
    const ue = (Q == null ? void 0 : Q.selectable) ?? !0;
    for (const oe of M.current) {
      const Pe = W.get(oe);
      if (Pe)
        for (const { edgeId: Ft } of Pe.values()) {
          const kt = R.get(Ft);
          kt && (kt.selectable ?? ue) && P.current.add(Ft);
        }
    }
    if (!If(Z, M.current)) {
      const oe = Gn(O, M.current, !0);
      U(oe);
    }
    if (!If(ie, P.current)) {
      const oe = Gn(R, P.current);
      X(oe);
    }
    w.setState({
      userSelectionRect: q,
      userSelectionActive: !0,
      nodesSelectionActive: !1
    });
  }, z = (N) => {
    var C, L;
    N.button === 0 && ((L = (C = N.target) == null ? void 0 : C.releasePointerCapture) == null || L.call(C, N.pointerId), !x && N.target === S.current && w.getState().userSelectionRect && (j == null || j(N)), w.setState({
      userSelectionActive: !1,
      userSelectionRect: null
    }), T.current && (l == null || l(N), w.setState({
      nodesSelectionActive: M.current.size > 0
    })));
  }, $ = r === !0 || Array.isArray(r) && r.includes(0);
  return _.jsxs("div", { className: xe(["react-flow__pane", { draggable: $, dragging: g, selection: e }]), onClick: v ? void 0 : kl(j, S), onContextMenu: kl(I, S), onWheel: kl(D, S), onPointerEnter: v ? void 0 : c, onPointerMove: v ? A : f, onPointerUp: v ? z : void 0, onPointerDownCapture: v ? k : void 0, onClickCapture: v ? H : void 0, onPointerLeave: m, ref: S, style: Fs, children: [y, _.jsx(IE, {})] });
}
function Lu({ id: e, store: t, unselect: n = !1, nodeRef: r }) {
  const { addSelectedNodes: o, unselectNodesAndEdges: i, multiSelectionActive: s, nodeLookup: l, onError: u } = t.getState(), a = l.get(e);
  if (!a) {
    u == null || u("012", _t.error012(e));
    return;
  }
  t.setState({ nodesSelectionActive: !1 }), a.selected ? (n || a.selected && s) && (i({ nodes: [a], edges: [] }), requestAnimationFrame(() => {
    var d;
    return (d = r == null ? void 0 : r.current) == null ? void 0 : d.blur();
  })) : o([e]);
}
function Wg({ nodeRef: e, disabled: t = !1, noDragClassName: n, handleSelector: r, nodeId: o, isSelectable: i, nodeClickDistance: s }) {
  const l = he(), [u, a] = F.useState(!1), d = F.useRef();
  return F.useEffect(() => {
    d.current = o_({
      getStoreItems: () => l.getState(),
      onNodeMouseDown: (c) => {
        Lu({
          id: c,
          store: l,
          nodeRef: e
        });
      },
      onDragStart: () => {
        a(!0);
      },
      onDragStop: () => {
        a(!1);
      }
    });
  }, []), F.useEffect(() => {
    if (!(t || !e.current || !d.current))
      return d.current.update({
        noDragClassName: n,
        handleSelector: r,
        domNode: e.current,
        isSelectable: i,
        nodeId: o,
        nodeClickDistance: s
      }), () => {
        var c;
        (c = d.current) == null || c.destroy();
      };
  }, [n, r, t, i, e, o, s]), u;
}
const AE = (e) => (t) => t.selected && (t.draggable || e && typeof t.draggable > "u");
function Yg() {
  const e = he();
  return F.useCallback((n) => {
    const { nodeExtent: r, snapToGrid: o, snapGrid: i, nodesDraggable: s, onError: l, updateNodePositions: u, nodeLookup: a, nodeOrigin: d } = e.getState(), c = /* @__PURE__ */ new Map(), f = AE(s), m = o ? i[0] : 5, y = o ? i[1] : 5, w = n.direction.x * m * n.factor, x = n.direction.y * y * n.factor;
    for (const [, p] of a) {
      if (!f(p))
        continue;
      let g = {
        x: p.internals.positionAbsolute.x + w,
        y: p.internals.positionAbsolute.y + x
      };
      o && (g = Oo(g, i));
      const { position: h, positionAbsolute: v } = ug({
        nodeId: p.id,
        nextPosition: g,
        nodeLookup: a,
        nodeExtent: r,
        nodeOrigin: d,
        onError: l
      });
      p.position = h, p.internals.positionAbsolute = v, c.set(p.id, p);
    }
    u(c);
  }, []);
}
const Wa = F.createContext(null), $E = Wa.Provider;
Wa.Consumer;
const Xg = () => F.useContext(Wa), DE = (e) => ({
  connectOnClick: e.connectOnClick,
  noPanClassName: e.noPanClassName,
  rfId: e.rfId
}), RE = (e, t, n) => (r) => {
  const { connectionClickStartHandle: o, connectionMode: i, connection: s } = r, { fromHandle: l, toHandle: u, isValid: a } = s, d = (u == null ? void 0 : u.nodeId) === e && (u == null ? void 0 : u.id) === t && (u == null ? void 0 : u.type) === n;
  return {
    connectingFrom: (l == null ? void 0 : l.nodeId) === e && (l == null ? void 0 : l.id) === t && (l == null ? void 0 : l.type) === n,
    connectingTo: d,
    clickConnecting: (o == null ? void 0 : o.nodeId) === e && (o == null ? void 0 : o.id) === t && (o == null ? void 0 : o.type) === n,
    isPossibleEndHandle: i === pr.Strict ? (l == null ? void 0 : l.type) !== n : e !== (l == null ? void 0 : l.nodeId) || t !== (l == null ? void 0 : l.id),
    connectionInProcess: !!l,
    clickConnectionInProcess: !!o,
    valid: d && a
  };
};
function jE({ type: e = "source", position: t = b.Top, isValidConnection: n, isConnectable: r = !0, isConnectableStart: o = !0, isConnectableEnd: i = !0, id: s, onConnect: l, children: u, className: a, onMouseDown: d, onTouchStart: c, ...f }, m) {
  var A, z;
  const y = s || null, w = e === "target", x = he(), p = Xg(), { connectOnClick: g, noPanClassName: h, rfId: v } = ne(DE, de), { connectingFrom: S, connectingTo: E, clickConnecting: M, isPossibleEndHandle: P, connectionInProcess: T, clickConnectionInProcess: j, valid: I } = ne(RE(p, y, e), de);
  p || (z = (A = x.getState()).onError) == null || z.call(A, "010", _t.error010());
  const D = ($) => {
    const { defaultEdgeOptions: N, onConnect: C, hasDefaultEdges: L } = x.getState(), O = {
      ...N,
      ...$
    };
    if (L) {
      const { edges: R, setEdges: W } = x.getState();
      W(BS(O, R));
    }
    C == null || C(O), l == null || l(O);
  }, H = ($) => {
    if (!p)
      return;
    const N = mg($.nativeEvent);
    if (o && (N && $.button === 0 || !N)) {
      const C = x.getState();
      Iu.onPointerDown($.nativeEvent, {
        handleDomNode: $.currentTarget,
        autoPanOnConnect: C.autoPanOnConnect,
        connectionMode: C.connectionMode,
        connectionRadius: C.connectionRadius,
        domNode: C.domNode,
        nodeLookup: C.nodeLookup,
        lib: C.lib,
        isTarget: w,
        handleId: y,
        nodeId: p,
        flowId: C.rfId,
        panBy: C.panBy,
        cancelConnection: C.cancelConnection,
        onConnectStart: C.onConnectStart,
        onConnectEnd: (...L) => {
          var O, R;
          return (R = (O = x.getState()).onConnectEnd) == null ? void 0 : R.call(O, ...L);
        },
        updateConnection: C.updateConnection,
        onConnect: D,
        isValidConnection: n || ((...L) => {
          var O, R;
          return ((R = (O = x.getState()).isValidConnection) == null ? void 0 : R.call(O, ...L)) ?? !0;
        }),
        getTransform: () => x.getState().transform,
        getFromHandle: () => x.getState().connection.fromHandle,
        autoPanSpeed: C.autoPanSpeed,
        dragThreshold: C.connectionDragThreshold
      });
    }
    N ? d == null || d($) : c == null || c($);
  }, k = ($) => {
    const { onClickConnectStart: N, onClickConnectEnd: C, connectionClickStartHandle: L, connectionMode: O, isValidConnection: R, lib: W, rfId: U, nodeLookup: X, connection: Q } = x.getState();
    if (!p || !L && !o)
      return;
    if (!L) {
      N == null || N($.nativeEvent, { nodeId: p, handleId: y, handleType: e }), x.setState({ connectionClickStartHandle: { nodeId: p, type: e, id: y } });
      return;
    }
    const K = pg($.target), B = n || R, { connection: G, isValid: ee } = Iu.isValid($.nativeEvent, {
      handle: {
        nodeId: p,
        id: y,
        type: e
      },
      connectionMode: O,
      fromNodeId: L.nodeId,
      fromHandleId: L.id || null,
      fromType: L.type,
      isValidConnection: B,
      flowId: U,
      doc: K,
      lib: W,
      nodeLookup: X
    });
    ee && G && D(G);
    const J = structuredClone(Q);
    delete J.inProgress, J.toPosition = J.toHandle ? J.toHandle.position : null, C == null || C($, J), x.setState({ connectionClickStartHandle: null });
  };
  return _.jsx("div", { "data-handleid": y, "data-nodeid": p, "data-handlepos": t, "data-id": `${v}-${p}-${y}-${e}`, className: xe([
    "react-flow__handle",
    `react-flow__handle-${t}`,
    "nodrag",
    h,
    a,
    {
      source: !w,
      target: w,
      connectable: r,
      connectablestart: o,
      connectableend: i,
      clickconnecting: M,
      connectingfrom: S,
      connectingto: E,
      valid: I,
      /*
       * shows where you can start a connection from
       * and where you can end it while connecting
       */
      connectionindicator: r && (!T || P) && (T || j ? i : o)
    }
  ]), onMouseDown: H, onTouchStart: H, onClick: g ? k : void 0, ref: m, ...f, children: u });
}
const xr = F.memo(Bg(jE));
function OE({ data: e, isConnectable: t, sourcePosition: n = b.Bottom }) {
  return _.jsxs(_.Fragment, { children: [e == null ? void 0 : e.label, _.jsx(xr, { type: "source", position: n, isConnectable: t })] });
}
function FE({ data: e, isConnectable: t, targetPosition: n = b.Top, sourcePosition: r = b.Bottom }) {
  return _.jsxs(_.Fragment, { children: [_.jsx(xr, { type: "target", position: n, isConnectable: t }), e == null ? void 0 : e.label, _.jsx(xr, { type: "source", position: r, isConnectable: t })] });
}
function HE() {
  return null;
}
function VE({ data: e, isConnectable: t, targetPosition: n = b.Top }) {
  return _.jsxs(_.Fragment, { children: [_.jsx(xr, { type: "target", position: n, isConnectable: t }), e == null ? void 0 : e.label] });
}
const cs = {
  ArrowUp: { x: 0, y: -1 },
  ArrowDown: { x: 0, y: 1 },
  ArrowLeft: { x: -1, y: 0 },
  ArrowRight: { x: 1, y: 0 }
}, nd = {
  input: OE,
  default: FE,
  output: VE,
  group: HE
};
function BE(e) {
  var t, n, r, o;
  return e.internals.handleBounds === void 0 ? {
    width: e.width ?? e.initialWidth ?? ((t = e.style) == null ? void 0 : t.width),
    height: e.height ?? e.initialHeight ?? ((n = e.style) == null ? void 0 : n.height)
  } : {
    width: e.width ?? ((r = e.style) == null ? void 0 : r.width),
    height: e.height ?? ((o = e.style) == null ? void 0 : o.height)
  };
}
const UE = (e) => {
  const { width: t, height: n, x: r, y: o } = jo(e.nodeLookup, {
    filter: (i) => !!i.selected
  });
  return {
    width: at(t) ? t : null,
    height: at(n) ? n : null,
    userSelectionActive: e.userSelectionActive,
    transformString: `translate(${e.transform[0]}px,${e.transform[1]}px) scale(${e.transform[2]}) translate(${r}px,${o}px)`
  };
};
function WE({ onSelectionContextMenu: e, noPanClassName: t, disableKeyboardA11y: n }) {
  const r = he(), { width: o, height: i, transformString: s, userSelectionActive: l } = ne(UE, de), u = Yg(), a = F.useRef(null);
  F.useEffect(() => {
    var m;
    n || (m = a.current) == null || m.focus({
      preventScroll: !0
    });
  }, [n]);
  const d = !l && o !== null && i !== null;
  if (Wg({
    nodeRef: a,
    disabled: !d
  }), !d)
    return null;
  const c = e ? (m) => {
    const y = r.getState().nodes.filter((w) => w.selected);
    e(m, y);
  } : void 0, f = (m) => {
    Object.prototype.hasOwnProperty.call(cs, m.key) && (m.preventDefault(), u({
      direction: cs[m.key],
      factor: m.shiftKey ? 4 : 1
    }));
  };
  return _.jsx("div", { className: xe(["react-flow__nodesselection", "react-flow__container", t]), style: {
    transform: s
  }, children: _.jsx("div", { ref: a, className: "react-flow__nodesselection-rect", onContextMenu: c, tabIndex: n ? void 0 : -1, onKeyDown: n ? void 0 : f, style: {
    width: o,
    height: i
  } }) });
}
const rd = typeof window < "u" ? window : void 0, YE = (e) => ({ nodesSelectionActive: e.nodesSelectionActive, userSelectionActive: e.userSelectionActive });
function Qg({ children: e, onPaneClick: t, onPaneMouseEnter: n, onPaneMouseMove: r, onPaneMouseLeave: o, onPaneContextMenu: i, onPaneScroll: s, paneClickDistance: l, deleteKeyCode: u, selectionKeyCode: a, selectionOnDrag: d, selectionMode: c, onSelectionStart: f, onSelectionEnd: m, multiSelectionKeyCode: y, panActivationKeyCode: w, zoomActivationKeyCode: x, elementsSelectable: p, zoomOnScroll: g, zoomOnPinch: h, panOnScroll: v, panOnScrollSpeed: S, panOnScrollMode: E, zoomOnDoubleClick: M, panOnDrag: P, defaultViewport: T, translateExtent: j, minZoom: I, maxZoom: D, preventScrolling: H, onSelectionContextMenu: k, noWheelClassName: A, noPanClassName: z, disableKeyboardA11y: $, onViewportChange: N, isControlledViewport: C }) {
  const { nodesSelectionActive: L, userSelectionActive: O } = ne(YE, de), R = Po(a, { target: rd }), W = Po(w, { target: rd }), U = W || P, X = W || v, Q = d && U !== !0, K = R || O || Q;
  return NE({ deleteKeyCode: u, multiSelectionKeyCode: y }), _.jsx(PE, { onPaneContextMenu: i, elementsSelectable: p, zoomOnScroll: g, zoomOnPinch: h, panOnScroll: X, panOnScrollSpeed: S, panOnScrollMode: E, zoomOnDoubleClick: M, panOnDrag: !R && U, defaultViewport: T, translateExtent: j, minZoom: I, maxZoom: D, zoomActivationKeyCode: x, preventScrolling: H, noWheelClassName: A, noPanClassName: z, onViewportChange: N, isControlledViewport: C, paneClickDistance: l, selectionOnDrag: Q, children: _.jsxs(LE, { onSelectionStart: f, onSelectionEnd: m, onPaneClick: t, onPaneMouseEnter: n, onPaneMouseMove: r, onPaneMouseLeave: o, onPaneContextMenu: i, onPaneScroll: s, panOnDrag: U, isSelecting: !!K, selectionMode: c, selectionKeyPressed: R, paneClickDistance: l, selectionOnDrag: Q, children: [e, L && _.jsx(WE, { onSelectionContextMenu: k, noPanClassName: z, disableKeyboardA11y: $ })] }) });
}
Qg.displayName = "FlowRenderer";
const XE = F.memo(Qg), QE = (e) => (t) => e ? Ra(t.nodeLookup, { x: 0, y: 0, width: t.width, height: t.height }, t.transform, !0).map((n) => n.id) : Array.from(t.nodeLookup.keys());
function bE(e) {
  return ne(F.useCallback(QE(e), [e]), de);
}
const KE = (e) => e.updateNodeInternals;
function GE() {
  const e = ne(KE), [t] = F.useState(() => typeof ResizeObserver > "u" ? null : new ResizeObserver((n) => {
    const r = /* @__PURE__ */ new Map();
    n.forEach((o) => {
      const i = o.target.getAttribute("data-id");
      r.set(i, {
        id: i,
        nodeElement: o.target,
        force: !0
      });
    }), e(r);
  }));
  return F.useEffect(() => () => {
    t == null || t.disconnect();
  }, [t]), t;
}
function ZE({ node: e, nodeType: t, hasDimensions: n, resizeObserver: r }) {
  const o = he(), i = F.useRef(null), s = F.useRef(null), l = F.useRef(e.sourcePosition), u = F.useRef(e.targetPosition), a = F.useRef(t), d = n && !!e.internals.handleBounds;
  return F.useEffect(() => {
    i.current && !e.hidden && (!d || s.current !== i.current) && (s.current && (r == null || r.unobserve(s.current)), r == null || r.observe(i.current), s.current = i.current);
  }, [d, e.hidden]), F.useEffect(() => () => {
    s.current && (r == null || r.unobserve(s.current), s.current = null);
  }, []), F.useEffect(() => {
    if (i.current) {
      const c = a.current !== t, f = l.current !== e.sourcePosition, m = u.current !== e.targetPosition;
      (c || f || m) && (a.current = t, l.current = e.sourcePosition, u.current = e.targetPosition, o.getState().updateNodeInternals(/* @__PURE__ */ new Map([[e.id, { id: e.id, nodeElement: i.current, force: !0 }]])));
    }
  }, [e.id, t, e.sourcePosition, e.targetPosition]), i;
}
function qE({ id: e, onClick: t, onMouseEnter: n, onMouseMove: r, onMouseLeave: o, onContextMenu: i, onDoubleClick: s, nodesDraggable: l, elementsSelectable: u, nodesConnectable: a, nodesFocusable: d, resizeObserver: c, noDragClassName: f, noPanClassName: m, disableKeyboardA11y: y, rfId: w, nodeTypes: x, nodeClickDistance: p, onError: g }) {
  const { node: h, internals: v, isParent: S } = ne((B) => {
    const G = B.nodeLookup.get(e), ee = B.parentLookup.has(e);
    return {
      node: G,
      internals: G.internals,
      isParent: ee
    };
  }, de);
  let E = h.type || "default", M = (x == null ? void 0 : x[E]) || nd[E];
  M === void 0 && (g == null || g("003", _t.error003(E)), E = "default", M = (x == null ? void 0 : x.default) || nd.default);
  const P = !!(h.draggable || l && typeof h.draggable > "u"), T = !!(h.selectable || u && typeof h.selectable > "u"), j = !!(h.connectable || a && typeof h.connectable > "u"), I = !!(h.focusable || d && typeof h.focusable > "u"), D = he(), H = dg(h), k = ZE({ node: h, nodeType: E, hasDimensions: H, resizeObserver: c }), A = Wg({
    nodeRef: k,
    disabled: h.hidden || !P,
    noDragClassName: f,
    handleSelector: h.dragHandle,
    nodeId: e,
    isSelectable: T,
    nodeClickDistance: p
  }), z = Yg();
  if (h.hidden)
    return null;
  const $ = Ot(h), N = BE(h), C = T || P || t || n || r || o, L = n ? (B) => n(B, { ...v.userNode }) : void 0, O = r ? (B) => r(B, { ...v.userNode }) : void 0, R = o ? (B) => o(B, { ...v.userNode }) : void 0, W = i ? (B) => i(B, { ...v.userNode }) : void 0, U = s ? (B) => s(B, { ...v.userNode }) : void 0, X = (B) => {
    const { selectNodesOnDrag: G, nodeDragThreshold: ee } = D.getState();
    T && (!G || !P || ee > 0) && Lu({
      id: e,
      store: D,
      nodeRef: k
    }), t && t(B, { ...v.userNode });
  }, Q = (B) => {
    if (!(gg(B.nativeEvent) || y)) {
      if (rg.includes(B.key) && T) {
        const G = B.key === "Escape";
        Lu({
          id: e,
          store: D,
          unselect: G,
          nodeRef: k
        });
      } else if (P && h.selected && Object.prototype.hasOwnProperty.call(cs, B.key)) {
        B.preventDefault();
        const { ariaLabelConfig: G } = D.getState();
        D.setState({
          ariaLiveMessage: G["node.a11yDescription.ariaLiveMessage"]({
            direction: B.key.replace("Arrow", "").toLowerCase(),
            x: ~~v.positionAbsolute.x,
            y: ~~v.positionAbsolute.y
          })
        }), z({
          direction: cs[B.key],
          factor: B.shiftKey ? 4 : 1
        });
      }
    }
  }, K = () => {
    var ie;
    if (y || !((ie = k.current) != null && ie.matches(":focus-visible")))
      return;
    const { transform: B, width: G, height: ee, autoPanOnNodeFocus: J, setCenter: q } = D.getState();
    if (!J)
      return;
    Ra(/* @__PURE__ */ new Map([[e, h]]), { x: 0, y: 0, width: G, height: ee }, B, !0).length > 0 || q(h.position.x + $.width / 2, h.position.y + $.height / 2, {
      zoom: B[2]
    });
  };
  return _.jsx("div", { className: xe([
    "react-flow__node",
    `react-flow__node-${E}`,
    {
      // this is overwritable by passing `nopan` as a class name
      [m]: P
    },
    h.className,
    {
      selected: h.selected,
      selectable: T,
      parent: S,
      draggable: P,
      dragging: A
    }
  ]), ref: k, style: {
    zIndex: v.z,
    transform: `translate(${v.positionAbsolute.x}px,${v.positionAbsolute.y}px)`,
    pointerEvents: C ? "all" : "none",
    visibility: H ? "visible" : "hidden",
    ...h.style,
    ...N
  }, "data-id": e, "data-testid": `rf__node-${e}`, onMouseEnter: L, onMouseMove: O, onMouseLeave: R, onContextMenu: W, onClick: X, onDoubleClick: U, onKeyDown: I ? Q : void 0, tabIndex: I ? 0 : void 0, onFocus: I ? K : void 0, role: h.ariaRole ?? (I ? "group" : void 0), "aria-roledescription": "node", "aria-describedby": y ? void 0 : `${Og}-${w}`, "aria-label": h.ariaLabel, ...h.domAttributes, children: _.jsx($E, { value: e, children: _.jsx(M, { id: e, data: h.data, type: E, positionAbsoluteX: v.positionAbsolute.x, positionAbsoluteY: v.positionAbsolute.y, selected: h.selected ?? !1, selectable: T, draggable: P, deletable: h.deletable ?? !0, isConnectable: j, sourcePosition: h.sourcePosition, targetPosition: h.targetPosition, dragging: A, dragHandle: h.dragHandle, zIndex: v.z, parentId: h.parentId, ...$ }) }) });
}
var JE = F.memo(qE);
const e2 = (e) => ({
  nodesDraggable: e.nodesDraggable,
  nodesConnectable: e.nodesConnectable,
  nodesFocusable: e.nodesFocusable,
  elementsSelectable: e.elementsSelectable,
  onError: e.onError
});
function bg(e) {
  const { nodesDraggable: t, nodesConnectable: n, nodesFocusable: r, elementsSelectable: o, onError: i } = ne(e2, de), s = bE(e.onlyRenderVisibleElements), l = GE();
  return _.jsx("div", { className: "react-flow__nodes", style: Fs, children: s.map((u) => (
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
    _.jsx(JE, { id: u, nodeTypes: e.nodeTypes, nodeExtent: e.nodeExtent, onClick: e.onNodeClick, onMouseEnter: e.onNodeMouseEnter, onMouseMove: e.onNodeMouseMove, onMouseLeave: e.onNodeMouseLeave, onContextMenu: e.onNodeContextMenu, onDoubleClick: e.onNodeDoubleClick, noDragClassName: e.noDragClassName, noPanClassName: e.noPanClassName, rfId: e.rfId, disableKeyboardA11y: e.disableKeyboardA11y, resizeObserver: l, nodesDraggable: t, nodesConnectable: n, nodesFocusable: r, elementsSelectable: o, nodeClickDistance: e.nodeClickDistance, onError: i }, u)
  )) });
}
bg.displayName = "NodeRenderer";
const t2 = F.memo(bg);
function n2(e) {
  return ne(F.useCallback((n) => {
    if (!e)
      return n.edges.map((o) => o.id);
    const r = [];
    if (n.width && n.height)
      for (const o of n.edges) {
        const i = n.nodeLookup.get(o.source), s = n.nodeLookup.get(o.target);
        i && s && FS({
          sourceNode: i,
          targetNode: s,
          width: n.width,
          height: n.height,
          transform: n.transform
        }) && r.push(o.id);
      }
    return r;
  }, [e]), de);
}
const r2 = ({ color: e = "none", strokeWidth: t = 1 }) => {
  const n = {
    strokeWidth: t,
    ...e && { stroke: e }
  };
  return _.jsx("polyline", { className: "arrow", style: n, strokeLinecap: "round", fill: "none", strokeLinejoin: "round", points: "-5,-4 0,0 -5,4" });
}, o2 = ({ color: e = "none", strokeWidth: t = 1 }) => {
  const n = {
    strokeWidth: t,
    ...e && { stroke: e, fill: e }
  };
  return _.jsx("polyline", { className: "arrowclosed", style: n, strokeLinecap: "round", strokeLinejoin: "round", points: "-5,-4 0,0 -5,4 -5,-4" });
}, od = {
  [No.Arrow]: r2,
  [No.ArrowClosed]: o2
};
function i2(e) {
  const t = he();
  return F.useMemo(() => {
    var o, i;
    return Object.prototype.hasOwnProperty.call(od, e) ? od[e] : ((i = (o = t.getState()).onError) == null || i.call(o, "009", _t.error009(e)), null);
  }, [e]);
}
const s2 = ({ id: e, type: t, color: n, width: r = 12.5, height: o = 12.5, markerUnits: i = "strokeWidth", strokeWidth: s, orient: l = "auto-start-reverse" }) => {
  const u = i2(t);
  return u ? _.jsx("marker", { className: "react-flow__arrowhead", id: e, markerWidth: `${r}`, markerHeight: `${o}`, viewBox: "-10 -10 20 20", markerUnits: i, orient: l, refX: "0", refY: "0", children: _.jsx(u, { color: n, strokeWidth: s }) }) : null;
}, Kg = ({ defaultColor: e, rfId: t }) => {
  const n = ne((i) => i.edges), r = ne((i) => i.defaultEdgeOptions), o = F.useMemo(() => QS(n, {
    id: t,
    defaultColor: e,
    defaultMarkerStart: r == null ? void 0 : r.markerStart,
    defaultMarkerEnd: r == null ? void 0 : r.markerEnd
  }), [n, r, t, e]);
  return o.length ? _.jsx("svg", { className: "react-flow__marker", "aria-hidden": "true", children: _.jsx("defs", { children: o.map((i) => _.jsx(s2, { id: i.id, type: i.type, color: i.color, width: i.width, height: i.height, markerUnits: i.markerUnits, strokeWidth: i.strokeWidth, orient: i.orient }, i.id)) }) }) : null;
};
Kg.displayName = "MarkerDefinitions";
var l2 = F.memo(Kg);
function Gg({ x: e, y: t, label: n, labelStyle: r, labelShowBg: o = !0, labelBgStyle: i, labelBgPadding: s = [2, 4], labelBgBorderRadius: l = 2, children: u, className: a, ...d }) {
  const [c, f] = F.useState({ x: 1, y: 0, width: 0, height: 0 }), m = xe(["react-flow__edge-textwrapper", a]), y = F.useRef(null);
  return F.useEffect(() => {
    if (y.current) {
      const w = y.current.getBBox();
      f({
        x: w.x,
        y: w.y,
        width: w.width,
        height: w.height
      });
    }
  }, [n]), n ? _.jsxs("g", { transform: `translate(${e - c.width / 2} ${t - c.height / 2})`, className: m, visibility: c.width ? "visible" : "hidden", ...d, children: [o && _.jsx("rect", { width: c.width + 2 * s[0], x: -s[0], y: -s[1], height: c.height + 2 * s[1], className: "react-flow__edge-textbg", style: i, rx: l, ry: l }), _.jsx("text", { className: "react-flow__edge-text", y: c.height / 2, dy: "0.3em", ref: y, style: r, children: n }), u] }) : null;
}
Gg.displayName = "EdgeText";
const u2 = F.memo(Gg);
function Hs({ path: e, labelX: t, labelY: n, label: r, labelStyle: o, labelShowBg: i, labelBgStyle: s, labelBgPadding: l, labelBgBorderRadius: u, interactionWidth: a = 20, ...d }) {
  return _.jsxs(_.Fragment, { children: [_.jsx("path", { ...d, d: e, fill: "none", className: xe(["react-flow__edge-path", d.className]) }), a ? _.jsx("path", { d: e, fill: "none", strokeOpacity: 0, strokeWidth: a, className: "react-flow__edge-interaction" }) : null, r && at(t) && at(n) ? _.jsx(u2, { x: t, y: n, label: r, labelStyle: o, labelShowBg: i, labelBgStyle: s, labelBgPadding: l, labelBgBorderRadius: u }) : null] });
}
function id({ pos: e, x1: t, y1: n, x2: r, y2: o }) {
  return e === b.Left || e === b.Right ? [0.5 * (t + r), n] : [t, 0.5 * (n + o)];
}
function Zg({ sourceX: e, sourceY: t, sourcePosition: n = b.Bottom, targetX: r, targetY: o, targetPosition: i = b.Top }) {
  const [s, l] = id({
    pos: n,
    x1: e,
    y1: t,
    x2: r,
    y2: o
  }), [u, a] = id({
    pos: i,
    x1: r,
    y1: o,
    x2: e,
    y2: t
  }), [d, c, f, m] = yg({
    sourceX: e,
    sourceY: t,
    targetX: r,
    targetY: o,
    sourceControlX: s,
    sourceControlY: l,
    targetControlX: u,
    targetControlY: a
  });
  return [
    `M${e},${t} C${s},${l} ${u},${a} ${r},${o}`,
    d,
    c,
    f,
    m
  ];
}
function qg(e) {
  return F.memo(({ id: t, sourceX: n, sourceY: r, targetX: o, targetY: i, sourcePosition: s, targetPosition: l, label: u, labelStyle: a, labelShowBg: d, labelBgStyle: c, labelBgPadding: f, labelBgBorderRadius: m, style: y, markerEnd: w, markerStart: x, interactionWidth: p }) => {
    const [g, h, v] = Zg({
      sourceX: n,
      sourceY: r,
      sourcePosition: s,
      targetX: o,
      targetY: i,
      targetPosition: l
    }), S = e.isInternal ? void 0 : t;
    return _.jsx(Hs, { id: S, path: g, labelX: h, labelY: v, label: u, labelStyle: a, labelShowBg: d, labelBgStyle: c, labelBgPadding: f, labelBgBorderRadius: m, style: y, markerEnd: w, markerStart: x, interactionWidth: p });
  });
}
const a2 = qg({ isInternal: !1 }), Jg = qg({ isInternal: !0 });
a2.displayName = "SimpleBezierEdge";
Jg.displayName = "SimpleBezierEdgeInternal";
function em(e) {
  return F.memo(({ id: t, sourceX: n, sourceY: r, targetX: o, targetY: i, label: s, labelStyle: l, labelShowBg: u, labelBgStyle: a, labelBgPadding: d, labelBgBorderRadius: c, style: f, sourcePosition: m = b.Bottom, targetPosition: y = b.Top, markerEnd: w, markerStart: x, pathOptions: p, interactionWidth: g }) => {
    const [h, v, S] = Mu({
      sourceX: n,
      sourceY: r,
      sourcePosition: m,
      targetX: o,
      targetY: i,
      targetPosition: y,
      borderRadius: p == null ? void 0 : p.borderRadius,
      offset: p == null ? void 0 : p.offset,
      stepPosition: p == null ? void 0 : p.stepPosition
    }), E = e.isInternal ? void 0 : t;
    return _.jsx(Hs, { id: E, path: h, labelX: v, labelY: S, label: s, labelStyle: l, labelShowBg: u, labelBgStyle: a, labelBgPadding: d, labelBgBorderRadius: c, style: f, markerEnd: w, markerStart: x, interactionWidth: g });
  });
}
const tm = em({ isInternal: !1 }), nm = em({ isInternal: !0 });
tm.displayName = "SmoothStepEdge";
nm.displayName = "SmoothStepEdgeInternal";
function rm(e) {
  return F.memo(({ id: t, ...n }) => {
    var o;
    const r = e.isInternal ? void 0 : t;
    return _.jsx(tm, { ...n, id: r, pathOptions: F.useMemo(() => {
      var i;
      return { borderRadius: 0, offset: (i = n.pathOptions) == null ? void 0 : i.offset };
    }, [(o = n.pathOptions) == null ? void 0 : o.offset]) });
  });
}
const c2 = rm({ isInternal: !1 }), om = rm({ isInternal: !0 });
c2.displayName = "StepEdge";
om.displayName = "StepEdgeInternal";
function im(e) {
  return F.memo(({ id: t, sourceX: n, sourceY: r, targetX: o, targetY: i, label: s, labelStyle: l, labelShowBg: u, labelBgStyle: a, labelBgPadding: d, labelBgBorderRadius: c, style: f, markerEnd: m, markerStart: y, interactionWidth: w }) => {
    const [x, p, g] = xg({ sourceX: n, sourceY: r, targetX: o, targetY: i }), h = e.isInternal ? void 0 : t;
    return _.jsx(Hs, { id: h, path: x, labelX: p, labelY: g, label: s, labelStyle: l, labelShowBg: u, labelBgStyle: a, labelBgPadding: d, labelBgBorderRadius: c, style: f, markerEnd: m, markerStart: y, interactionWidth: w });
  });
}
const f2 = im({ isInternal: !1 }), sm = im({ isInternal: !0 });
f2.displayName = "StraightEdge";
sm.displayName = "StraightEdgeInternal";
function lm(e) {
  return F.memo(({ id: t, sourceX: n, sourceY: r, targetX: o, targetY: i, sourcePosition: s = b.Bottom, targetPosition: l = b.Top, label: u, labelStyle: a, labelShowBg: d, labelBgStyle: c, labelBgPadding: f, labelBgBorderRadius: m, style: y, markerEnd: w, markerStart: x, pathOptions: p, interactionWidth: g }) => {
    const [h, v, S] = vg({
      sourceX: n,
      sourceY: r,
      sourcePosition: s,
      targetX: o,
      targetY: i,
      targetPosition: l,
      curvature: p == null ? void 0 : p.curvature
    }), E = e.isInternal ? void 0 : t;
    return _.jsx(Hs, { id: E, path: h, labelX: v, labelY: S, label: u, labelStyle: a, labelShowBg: d, labelBgStyle: c, labelBgPadding: f, labelBgBorderRadius: m, style: y, markerEnd: w, markerStart: x, interactionWidth: g });
  });
}
const d2 = lm({ isInternal: !1 }), um = lm({ isInternal: !0 });
d2.displayName = "BezierEdge";
um.displayName = "BezierEdgeInternal";
const sd = {
  default: um,
  straight: sm,
  step: om,
  smoothstep: nm,
  simplebezier: Jg
}, ld = {
  sourceX: null,
  sourceY: null,
  targetX: null,
  targetY: null,
  sourcePosition: null,
  targetPosition: null
}, h2 = (e, t, n) => n === b.Left ? e - t : n === b.Right ? e + t : e, p2 = (e, t, n) => n === b.Top ? e - t : n === b.Bottom ? e + t : e, ud = "react-flow__edgeupdater";
function ad({ position: e, centerX: t, centerY: n, radius: r = 10, onMouseDown: o, onMouseEnter: i, onMouseOut: s, type: l }) {
  return _.jsx("circle", { onMouseDown: o, onMouseEnter: i, onMouseOut: s, className: xe([ud, `${ud}-${l}`]), cx: h2(t, r, e), cy: p2(n, r, e), r, stroke: "transparent", fill: "transparent" });
}
function g2({ isReconnectable: e, reconnectRadius: t, edge: n, sourceX: r, sourceY: o, targetX: i, targetY: s, sourcePosition: l, targetPosition: u, onReconnect: a, onReconnectStart: d, onReconnectEnd: c, setReconnecting: f, setUpdateHover: m }) {
  const y = he(), w = (v, S) => {
    if (v.button !== 0)
      return;
    const { autoPanOnConnect: E, domNode: M, connectionMode: P, connectionRadius: T, lib: j, onConnectStart: I, cancelConnection: D, nodeLookup: H, rfId: k, panBy: A, updateConnection: z } = y.getState(), $ = S.type === "target", N = (O, R) => {
      f(!1), c == null || c(O, n, S.type, R);
    }, C = (O) => a == null ? void 0 : a(n, O), L = (O, R) => {
      f(!0), d == null || d(v, n, S.type), I == null || I(O, R);
    };
    Iu.onPointerDown(v.nativeEvent, {
      autoPanOnConnect: E,
      connectionMode: P,
      connectionRadius: T,
      domNode: M,
      handleId: S.id,
      nodeId: S.nodeId,
      nodeLookup: H,
      isTarget: $,
      edgeUpdaterType: S.type,
      lib: j,
      flowId: k,
      cancelConnection: D,
      panBy: A,
      isValidConnection: (...O) => {
        var R, W;
        return ((W = (R = y.getState()).isValidConnection) == null ? void 0 : W.call(R, ...O)) ?? !0;
      },
      onConnect: C,
      onConnectStart: L,
      onConnectEnd: (...O) => {
        var R, W;
        return (W = (R = y.getState()).onConnectEnd) == null ? void 0 : W.call(R, ...O);
      },
      onReconnectEnd: N,
      updateConnection: z,
      getTransform: () => y.getState().transform,
      getFromHandle: () => y.getState().connection.fromHandle,
      dragThreshold: y.getState().connectionDragThreshold,
      handleDomNode: v.currentTarget
    });
  }, x = (v) => w(v, { nodeId: n.target, id: n.targetHandle ?? null, type: "target" }), p = (v) => w(v, { nodeId: n.source, id: n.sourceHandle ?? null, type: "source" }), g = () => m(!0), h = () => m(!1);
  return _.jsxs(_.Fragment, { children: [(e === !0 || e === "source") && _.jsx(ad, { position: l, centerX: r, centerY: o, radius: t, onMouseDown: x, onMouseEnter: g, onMouseOut: h, type: "source" }), (e === !0 || e === "target") && _.jsx(ad, { position: u, centerX: i, centerY: s, radius: t, onMouseDown: p, onMouseEnter: g, onMouseOut: h, type: "target" })] });
}
function m2({ id: e, edgesFocusable: t, edgesReconnectable: n, elementsSelectable: r, onClick: o, onDoubleClick: i, onContextMenu: s, onMouseEnter: l, onMouseMove: u, onMouseLeave: a, reconnectRadius: d, onReconnect: c, onReconnectStart: f, onReconnectEnd: m, rfId: y, edgeTypes: w, noPanClassName: x, onError: p, disableKeyboardA11y: g }) {
  let h = ne((q) => q.edgeLookup.get(e));
  const v = ne((q) => q.defaultEdgeOptions);
  h = v ? { ...v, ...h } : h;
  let S = h.type || "default", E = (w == null ? void 0 : w[S]) || sd[S];
  E === void 0 && (p == null || p("011", _t.error011(S)), S = "default", E = (w == null ? void 0 : w.default) || sd.default);
  const M = !!(h.focusable || t && typeof h.focusable > "u"), P = typeof c < "u" && (h.reconnectable || n && typeof h.reconnectable > "u"), T = !!(h.selectable || r && typeof h.selectable > "u"), j = F.useRef(null), [I, D] = F.useState(!1), [H, k] = F.useState(!1), A = he(), { zIndex: z, sourceX: $, sourceY: N, targetX: C, targetY: L, sourcePosition: O, targetPosition: R } = ne(F.useCallback((q) => {
    const Z = q.nodeLookup.get(h.source), ie = q.nodeLookup.get(h.target);
    if (!Z || !ie)
      return {
        zIndex: h.zIndex,
        ...ld
      };
    const ue = XS({
      id: e,
      sourceNode: Z,
      targetNode: ie,
      sourceHandle: h.sourceHandle || null,
      targetHandle: h.targetHandle || null,
      connectionMode: q.connectionMode,
      onError: p
    });
    return {
      zIndex: OS({
        selected: h.selected,
        zIndex: h.zIndex,
        sourceNode: Z,
        targetNode: ie,
        elevateOnSelect: q.elevateEdgesOnSelect,
        zIndexMode: q.zIndexMode
      }),
      ...ue || ld
    };
  }, [h.source, h.target, h.sourceHandle, h.targetHandle, h.selected, h.zIndex]), de), W = F.useMemo(() => h.markerStart ? `url('#${Pu(h.markerStart, y)}')` : void 0, [h.markerStart, y]), U = F.useMemo(() => h.markerEnd ? `url('#${Pu(h.markerEnd, y)}')` : void 0, [h.markerEnd, y]);
  if (h.hidden || $ === null || N === null || C === null || L === null)
    return null;
  const X = (q) => {
    var oe;
    const { addSelectedEdges: Z, unselectNodesAndEdges: ie, multiSelectionActive: ue } = A.getState();
    T && (A.setState({ nodesSelectionActive: !1 }), h.selected && ue ? (ie({ nodes: [], edges: [h] }), (oe = j.current) == null || oe.blur()) : Z([e])), o && o(q, h);
  }, Q = i ? (q) => {
    i(q, { ...h });
  } : void 0, K = s ? (q) => {
    s(q, { ...h });
  } : void 0, B = l ? (q) => {
    l(q, { ...h });
  } : void 0, G = u ? (q) => {
    u(q, { ...h });
  } : void 0, ee = a ? (q) => {
    a(q, { ...h });
  } : void 0, J = (q) => {
    var Z;
    if (!g && rg.includes(q.key) && T) {
      const { unselectNodesAndEdges: ie, addSelectedEdges: ue } = A.getState();
      q.key === "Escape" ? ((Z = j.current) == null || Z.blur(), ie({ edges: [h] })) : ue([e]);
    }
  };
  return _.jsx("svg", { style: { zIndex: z }, children: _.jsxs("g", { className: xe([
    "react-flow__edge",
    `react-flow__edge-${S}`,
    h.className,
    x,
    {
      selected: h.selected,
      animated: h.animated,
      inactive: !T && !o,
      updating: I,
      selectable: T
    }
  ]), onClick: X, onDoubleClick: Q, onContextMenu: K, onMouseEnter: B, onMouseMove: G, onMouseLeave: ee, onKeyDown: M ? J : void 0, tabIndex: M ? 0 : void 0, role: h.ariaRole ?? (M ? "group" : "img"), "aria-roledescription": "edge", "data-id": e, "data-testid": `rf__edge-${e}`, "aria-label": h.ariaLabel === null ? void 0 : h.ariaLabel || `Edge from ${h.source} to ${h.target}`, "aria-describedby": M ? `${Fg}-${y}` : void 0, ref: j, ...h.domAttributes, children: [!H && _.jsx(E, { id: e, source: h.source, target: h.target, type: h.type, selected: h.selected, animated: h.animated, selectable: T, deletable: h.deletable ?? !0, label: h.label, labelStyle: h.labelStyle, labelShowBg: h.labelShowBg, labelBgStyle: h.labelBgStyle, labelBgPadding: h.labelBgPadding, labelBgBorderRadius: h.labelBgBorderRadius, sourceX: $, sourceY: N, targetX: C, targetY: L, sourcePosition: O, targetPosition: R, data: h.data, style: h.style, sourceHandleId: h.sourceHandle, targetHandleId: h.targetHandle, markerStart: W, markerEnd: U, pathOptions: "pathOptions" in h ? h.pathOptions : void 0, interactionWidth: h.interactionWidth }), P && _.jsx(g2, { edge: h, isReconnectable: P, reconnectRadius: d, onReconnect: c, onReconnectStart: f, onReconnectEnd: m, sourceX: $, sourceY: N, targetX: C, targetY: L, sourcePosition: O, targetPosition: R, setUpdateHover: D, setReconnecting: k })] }) });
}
var y2 = F.memo(m2);
const v2 = (e) => ({
  edgesFocusable: e.edgesFocusable,
  edgesReconnectable: e.edgesReconnectable,
  elementsSelectable: e.elementsSelectable,
  connectionMode: e.connectionMode,
  onError: e.onError
});
function am({ defaultMarkerColor: e, onlyRenderVisibleElements: t, rfId: n, edgeTypes: r, noPanClassName: o, onReconnect: i, onEdgeContextMenu: s, onEdgeMouseEnter: l, onEdgeMouseMove: u, onEdgeMouseLeave: a, onEdgeClick: d, reconnectRadius: c, onEdgeDoubleClick: f, onReconnectStart: m, onReconnectEnd: y, disableKeyboardA11y: w }) {
  const { edgesFocusable: x, edgesReconnectable: p, elementsSelectable: g, onError: h } = ne(v2, de), v = n2(t);
  return _.jsxs("div", { className: "react-flow__edges", children: [_.jsx(l2, { defaultColor: e, rfId: n }), v.map((S) => _.jsx(y2, { id: S, edgesFocusable: x, edgesReconnectable: p, elementsSelectable: g, noPanClassName: o, onReconnect: i, onContextMenu: s, onMouseEnter: l, onMouseMove: u, onMouseLeave: a, onClick: d, reconnectRadius: c, onDoubleClick: f, onReconnectStart: m, onReconnectEnd: y, rfId: n, onError: h, edgeTypes: r, disableKeyboardA11y: w }, S))] });
}
am.displayName = "EdgeRenderer";
const w2 = F.memo(am), x2 = (e) => `translate(${e.transform[0]}px,${e.transform[1]}px) scale(${e.transform[2]})`;
function S2({ children: e }) {
  const t = ne(x2);
  return _.jsx("div", { className: "react-flow__viewport xyflow__viewport react-flow__container", style: { transform: t }, children: e });
}
function _2(e) {
  const t = Os(), n = F.useRef(!1);
  F.useEffect(() => {
    !n.current && t.viewportInitialized && e && (setTimeout(() => e(t), 1), n.current = !0);
  }, [e, t.viewportInitialized]);
}
const E2 = (e) => {
  var t;
  return (t = e.panZoom) == null ? void 0 : t.syncViewport;
};
function k2(e) {
  const t = ne(E2), n = he();
  return F.useEffect(() => {
    e && (t == null || t(e), n.setState({ transform: [e.x, e.y, e.zoom] }));
  }, [e, t]), null;
}
function N2(e) {
  return e.connection.inProgress ? { ...e.connection, to: Fo(e.connection.to, e.transform) } : { ...e.connection };
}
function C2(e) {
  return N2;
}
function M2(e) {
  const t = C2();
  return ne(t, de);
}
const P2 = (e) => ({
  nodesConnectable: e.nodesConnectable,
  isValid: e.connection.isValid,
  inProgress: e.connection.inProgress,
  width: e.width,
  height: e.height
});
function z2({ containerStyle: e, style: t, type: n, component: r }) {
  const { nodesConnectable: o, width: i, height: s, isValid: l, inProgress: u } = ne(P2, de);
  return !(i && o && u) ? null : _.jsx("svg", { style: e, width: i, height: s, className: "react-flow__connectionline react-flow__container", children: _.jsx("g", { className: xe(["react-flow__connection", sg(l)]), children: _.jsx(cm, { style: t, type: n, CustomComponent: r, isValid: l }) }) });
}
const cm = ({ style: e, type: t = Qt.Bezier, CustomComponent: n, isValid: r }) => {
  const { inProgress: o, from: i, fromNode: s, fromHandle: l, fromPosition: u, to: a, toNode: d, toHandle: c, toPosition: f, pointer: m } = M2();
  if (!o)
    return;
  if (n)
    return _.jsx(n, { connectionLineType: t, connectionLineStyle: e, fromNode: s, fromHandle: l, fromX: i.x, fromY: i.y, toX: a.x, toY: a.y, fromPosition: u, toPosition: f, connectionStatus: sg(r), toNode: d, toHandle: c, pointer: m });
  let y = "";
  const w = {
    sourceX: i.x,
    sourceY: i.y,
    sourcePosition: u,
    targetX: a.x,
    targetY: a.y,
    targetPosition: f
  };
  switch (t) {
    case Qt.Bezier:
      [y] = vg(w);
      break;
    case Qt.SimpleBezier:
      [y] = Zg(w);
      break;
    case Qt.Step:
      [y] = Mu({
        ...w,
        borderRadius: 0
      });
      break;
    case Qt.SmoothStep:
      [y] = Mu(w);
      break;
    default:
      [y] = xg(w);
  }
  return _.jsx("path", { d: y, fill: "none", className: "react-flow__connection-path", style: e });
};
cm.displayName = "ConnectionLine";
const I2 = {};
function cd(e = I2) {
  F.useRef(e), he(), F.useEffect(() => {
  }, [e]);
}
function T2() {
  he(), F.useRef(!1), F.useEffect(() => {
  }, []);
}
function fm({ nodeTypes: e, edgeTypes: t, onInit: n, onNodeClick: r, onEdgeClick: o, onNodeDoubleClick: i, onEdgeDoubleClick: s, onNodeMouseEnter: l, onNodeMouseMove: u, onNodeMouseLeave: a, onNodeContextMenu: d, onSelectionContextMenu: c, onSelectionStart: f, onSelectionEnd: m, connectionLineType: y, connectionLineStyle: w, connectionLineComponent: x, connectionLineContainerStyle: p, selectionKeyCode: g, selectionOnDrag: h, selectionMode: v, multiSelectionKeyCode: S, panActivationKeyCode: E, zoomActivationKeyCode: M, deleteKeyCode: P, onlyRenderVisibleElements: T, elementsSelectable: j, defaultViewport: I, translateExtent: D, minZoom: H, maxZoom: k, preventScrolling: A, defaultMarkerColor: z, zoomOnScroll: $, zoomOnPinch: N, panOnScroll: C, panOnScrollSpeed: L, panOnScrollMode: O, zoomOnDoubleClick: R, panOnDrag: W, onPaneClick: U, onPaneMouseEnter: X, onPaneMouseMove: Q, onPaneMouseLeave: K, onPaneScroll: B, onPaneContextMenu: G, paneClickDistance: ee, nodeClickDistance: J, onEdgeContextMenu: q, onEdgeMouseEnter: Z, onEdgeMouseMove: ie, onEdgeMouseLeave: ue, reconnectRadius: oe, onReconnect: Pe, onReconnectStart: Ft, onReconnectEnd: kt, noDragClassName: fn, noWheelClassName: kr, noPanClassName: Nr, disableKeyboardA11y: Cr, nodeExtent: Vs, rfId: Ho, viewport: $n, onViewportChange: Mr }) {
  return cd(e), cd(t), T2(), _2(n), k2($n), _.jsx(XE, { onPaneClick: U, onPaneMouseEnter: X, onPaneMouseMove: Q, onPaneMouseLeave: K, onPaneContextMenu: G, onPaneScroll: B, paneClickDistance: ee, deleteKeyCode: P, selectionKeyCode: g, selectionOnDrag: h, selectionMode: v, onSelectionStart: f, onSelectionEnd: m, multiSelectionKeyCode: S, panActivationKeyCode: E, zoomActivationKeyCode: M, elementsSelectable: j, zoomOnScroll: $, zoomOnPinch: N, zoomOnDoubleClick: R, panOnScroll: C, panOnScrollSpeed: L, panOnScrollMode: O, panOnDrag: W, defaultViewport: I, translateExtent: D, minZoom: H, maxZoom: k, onSelectionContextMenu: c, preventScrolling: A, noDragClassName: fn, noWheelClassName: kr, noPanClassName: Nr, disableKeyboardA11y: Cr, onViewportChange: Mr, isControlledViewport: !!$n, children: _.jsxs(S2, { children: [_.jsx(w2, { edgeTypes: t, onEdgeClick: o, onEdgeDoubleClick: s, onReconnect: Pe, onReconnectStart: Ft, onReconnectEnd: kt, onlyRenderVisibleElements: T, onEdgeContextMenu: q, onEdgeMouseEnter: Z, onEdgeMouseMove: ie, onEdgeMouseLeave: ue, reconnectRadius: oe, defaultMarkerColor: z, noPanClassName: Nr, disableKeyboardA11y: Cr, rfId: Ho }), _.jsx(z2, { style: w, type: y, component: x, containerStyle: p }), _.jsx("div", { className: "react-flow__edgelabel-renderer" }), _.jsx(t2, { nodeTypes: e, onNodeClick: r, onNodeDoubleClick: i, onNodeMouseEnter: l, onNodeMouseMove: u, onNodeMouseLeave: a, onNodeContextMenu: d, nodeClickDistance: J, onlyRenderVisibleElements: T, noPanClassName: Nr, noDragClassName: fn, disableKeyboardA11y: Cr, nodeExtent: Vs, rfId: Ho }), _.jsx("div", { className: "react-flow__viewport-portal" })] }) });
}
fm.displayName = "GraphView";
const L2 = F.memo(fm), fd = ({ nodes: e, edges: t, defaultNodes: n, defaultEdges: r, width: o, height: i, fitView: s, fitViewOptions: l, minZoom: u = 0.5, maxZoom: a = 2, nodeOrigin: d, nodeExtent: c, zIndexMode: f = "basic" } = {}) => {
  const m = /* @__PURE__ */ new Map(), y = /* @__PURE__ */ new Map(), w = /* @__PURE__ */ new Map(), x = /* @__PURE__ */ new Map(), p = r ?? t ?? [], g = n ?? e ?? [], h = d ?? [0, 0], v = c ?? Eo;
  Eg(w, x, p);
  const { nodesInitialized: S } = zu(g, m, y, {
    nodeOrigin: h,
    nodeExtent: v,
    zIndexMode: f
  });
  let E = [0, 0, 1];
  if (s && o && i) {
    const M = jo(m, {
      filter: (I) => !!((I.width || I.initialWidth) && (I.height || I.initialHeight))
    }), { x: P, y: T, zoom: j } = ja(M, o, i, u, a, (l == null ? void 0 : l.padding) ?? 0.1);
    E = [P, T, j];
  }
  return {
    rfId: "1",
    width: o ?? 0,
    height: i ?? 0,
    transform: E,
    nodes: g,
    nodesInitialized: S,
    nodeLookup: m,
    parentLookup: y,
    edges: p,
    edgeLookup: x,
    connectionLookup: w,
    onNodesChange: null,
    onEdgesChange: null,
    hasDefaultNodes: n !== void 0,
    hasDefaultEdges: r !== void 0,
    panZoom: null,
    minZoom: u,
    maxZoom: a,
    translateExtent: Eo,
    nodeExtent: v,
    nodesSelectionActive: !1,
    userSelectionActive: !1,
    userSelectionRect: null,
    connectionMode: pr.Strict,
    domNode: null,
    paneDragging: !1,
    noPanClassName: "nopan",
    nodeOrigin: h,
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
    fitViewQueued: s ?? !1,
    fitViewOptions: l,
    fitViewResolver: null,
    connection: { ...ig },
    connectionClickStartHandle: null,
    connectOnClick: !0,
    ariaLiveMessage: "",
    autoPanOnConnect: !0,
    autoPanOnNodeDrag: !0,
    autoPanOnNodeFocus: !0,
    autoPanSpeed: 15,
    connectionRadius: 20,
    onError: LS,
    isValidConnection: void 0,
    onSelectionChangeHandlers: [],
    lib: "react",
    debug: !1,
    ariaLabelConfig: og,
    zIndexMode: f,
    onNodesChangeMiddlewareMap: /* @__PURE__ */ new Map(),
    onEdgesChangeMiddlewareMap: /* @__PURE__ */ new Map()
  };
}, A2 = ({ nodes: e, edges: t, defaultNodes: n, defaultEdges: r, width: o, height: i, fitView: s, fitViewOptions: l, minZoom: u, maxZoom: a, nodeOrigin: d, nodeExtent: c, zIndexMode: f }) => G_((m, y) => {
  async function w() {
    const { nodeLookup: x, panZoom: p, fitViewOptions: g, fitViewResolver: h, width: v, height: S, minZoom: E, maxZoom: M } = y();
    p && (await IS({
      nodes: x,
      width: v,
      height: S,
      panZoom: p,
      minZoom: E,
      maxZoom: M
    }, g), h == null || h.resolve(!0), m({ fitViewResolver: null }));
  }
  return {
    ...fd({
      nodes: e,
      edges: t,
      width: o,
      height: i,
      fitView: s,
      fitViewOptions: l,
      minZoom: u,
      maxZoom: a,
      nodeOrigin: d,
      nodeExtent: c,
      defaultNodes: n,
      defaultEdges: r,
      zIndexMode: f
    }),
    setNodes: (x) => {
      const { nodeLookup: p, parentLookup: g, nodeOrigin: h, elevateNodesOnSelect: v, fitViewQueued: S, zIndexMode: E, nodesSelectionActive: M } = y(), { nodesInitialized: P, hasSelectedNodes: T } = zu(x, p, g, {
        nodeOrigin: h,
        nodeExtent: c,
        elevateNodesOnSelect: v,
        checkEquality: !0,
        zIndexMode: E
      }), j = M && T;
      S && P ? (w(), m({
        nodes: x,
        nodesInitialized: P,
        fitViewQueued: !1,
        fitViewOptions: void 0,
        nodesSelectionActive: j
      })) : m({ nodes: x, nodesInitialized: P, nodesSelectionActive: j });
    },
    setEdges: (x) => {
      const { connectionLookup: p, edgeLookup: g } = y();
      Eg(p, g, x), m({ edges: x });
    },
    setDefaultNodesAndEdges: (x, p) => {
      if (x) {
        const { setNodes: g } = y();
        g(x), m({ hasDefaultNodes: !0 });
      }
      if (p) {
        const { setEdges: g } = y();
        g(p), m({ hasDefaultEdges: !0 });
      }
    },
    /*
     * Every node gets registered at a ResizeObserver. Whenever a node
     * changes its dimensions, this function is called to measure the
     * new dimensions and update the nodes.
     */
    updateNodeInternals: (x) => {
      const { triggerNodeChanges: p, nodeLookup: g, parentLookup: h, domNode: v, nodeOrigin: S, nodeExtent: E, debug: M, fitViewQueued: P, zIndexMode: T } = y(), { changes: j, updatedInternals: I } = e_(x, g, h, v, S, E, T);
      I && (GS(g, h, { nodeOrigin: S, nodeExtent: E, zIndexMode: T }), P ? (w(), m({ fitViewQueued: !1, fitViewOptions: void 0 })) : m({}), (j == null ? void 0 : j.length) > 0 && (M && console.log("React Flow: trigger node changes", j), p == null || p(j)));
    },
    updateNodePositions: (x, p = !1) => {
      const g = [];
      let h = [];
      const { nodeLookup: v, triggerNodeChanges: S, connection: E, updateConnection: M, onNodesChangeMiddlewareMap: P } = y();
      for (const [T, j] of x) {
        const I = v.get(T), D = !!(I != null && I.expandParent && (I != null && I.parentId) && (j != null && j.position)), H = {
          id: T,
          type: "position",
          position: D ? {
            x: Math.max(0, j.position.x),
            y: Math.max(0, j.position.y)
          } : j.position,
          dragging: p
        };
        if (I && E.inProgress && E.fromNode.id === I.id) {
          const k = Tn(I, E.fromHandle, b.Left, !0);
          M({ ...E, from: k });
        }
        D && I.parentId && g.push({
          id: T,
          parentId: I.parentId,
          rect: {
            ...j.internals.positionAbsolute,
            width: j.measured.width ?? 0,
            height: j.measured.height ?? 0
          }
        }), h.push(H);
      }
      if (g.length > 0) {
        const { parentLookup: T, nodeOrigin: j } = y(), I = Ua(g, v, T, j);
        h.push(...I);
      }
      for (const T of P.values())
        h = T(h);
      S(h);
    },
    triggerNodeChanges: (x) => {
      const { onNodesChange: p, setNodes: g, nodes: h, hasDefaultNodes: v, debug: S } = y();
      if (x != null && x.length) {
        if (v) {
          const E = yE(x, h);
          g(E);
        }
        S && console.log("React Flow: trigger node changes", x), p == null || p(x);
      }
    },
    triggerEdgeChanges: (x) => {
      const { onEdgesChange: p, setEdges: g, edges: h, hasDefaultEdges: v, debug: S } = y();
      if (x != null && x.length) {
        if (v) {
          const E = vE(x, h);
          g(E);
        }
        S && console.log("React Flow: trigger edge changes", x), p == null || p(x);
      }
    },
    addSelectedNodes: (x) => {
      const { multiSelectionActive: p, edgeLookup: g, nodeLookup: h, triggerNodeChanges: v, triggerEdgeChanges: S } = y();
      if (p) {
        const E = x.map((M) => pn(M, !0));
        v(E);
        return;
      }
      v(Gn(h, /* @__PURE__ */ new Set([...x]), !0)), S(Gn(g));
    },
    addSelectedEdges: (x) => {
      const { multiSelectionActive: p, edgeLookup: g, nodeLookup: h, triggerNodeChanges: v, triggerEdgeChanges: S } = y();
      if (p) {
        const E = x.map((M) => pn(M, !0));
        S(E);
        return;
      }
      S(Gn(g, /* @__PURE__ */ new Set([...x]))), v(Gn(h, /* @__PURE__ */ new Set(), !0));
    },
    unselectNodesAndEdges: ({ nodes: x, edges: p } = {}) => {
      const { edges: g, nodes: h, nodeLookup: v, triggerNodeChanges: S, triggerEdgeChanges: E } = y(), M = x || h, P = p || g, T = [];
      for (const I of M) {
        if (!I.selected)
          continue;
        const D = v.get(I.id);
        D && (D.selected = !1), T.push(pn(I.id, !1));
      }
      const j = [];
      for (const I of P)
        I.selected && j.push(pn(I.id, !1));
      S(T), E(j);
    },
    setMinZoom: (x) => {
      const { panZoom: p, maxZoom: g } = y();
      p == null || p.setScaleExtent([x, g]), m({ minZoom: x });
    },
    setMaxZoom: (x) => {
      const { panZoom: p, minZoom: g } = y();
      p == null || p.setScaleExtent([g, x]), m({ maxZoom: x });
    },
    setTranslateExtent: (x) => {
      var p;
      (p = y().panZoom) == null || p.setTranslateExtent(x), m({ translateExtent: x });
    },
    resetSelectedElements: () => {
      const { edges: x, nodes: p, triggerNodeChanges: g, triggerEdgeChanges: h, elementsSelectable: v } = y();
      if (!v)
        return;
      const S = p.reduce((M, P) => P.selected ? [...M, pn(P.id, !1)] : M, []), E = x.reduce((M, P) => P.selected ? [...M, pn(P.id, !1)] : M, []);
      g(S), h(E);
    },
    setNodeExtent: (x) => {
      const { nodes: p, nodeLookup: g, parentLookup: h, nodeOrigin: v, elevateNodesOnSelect: S, nodeExtent: E, zIndexMode: M } = y();
      x[0][0] === E[0][0] && x[0][1] === E[0][1] && x[1][0] === E[1][0] && x[1][1] === E[1][1] || (zu(p, g, h, {
        nodeOrigin: v,
        nodeExtent: x,
        elevateNodesOnSelect: S,
        checkEquality: !1,
        zIndexMode: M
      }), m({ nodeExtent: x }));
    },
    panBy: (x) => {
      const { transform: p, width: g, height: h, panZoom: v, translateExtent: S } = y();
      return t_({ delta: x, panZoom: v, transform: p, translateExtent: S, width: g, height: h });
    },
    setCenter: async (x, p, g) => {
      const { width: h, height: v, maxZoom: S, panZoom: E } = y();
      if (!E)
        return Promise.resolve(!1);
      const M = typeof (g == null ? void 0 : g.zoom) < "u" ? g.zoom : S;
      return await E.setViewport({
        x: h / 2 - x * M,
        y: v / 2 - p * M,
        zoom: M
      }, { duration: g == null ? void 0 : g.duration, ease: g == null ? void 0 : g.ease, interpolate: g == null ? void 0 : g.interpolate }), Promise.resolve(!0);
    },
    cancelConnection: () => {
      m({
        connection: { ...ig }
      });
    },
    updateConnection: (x) => {
      m({ connection: x });
    },
    reset: () => m({ ...fd() })
  };
}, Object.is);
function dm({ initialNodes: e, initialEdges: t, defaultNodes: n, defaultEdges: r, initialWidth: o, initialHeight: i, initialMinZoom: s, initialMaxZoom: l, initialFitViewOptions: u, fitView: a, nodeOrigin: d, nodeExtent: c, zIndexMode: f, children: m }) {
  const [y] = F.useState(() => A2({
    nodes: e,
    edges: t,
    defaultNodes: n,
    defaultEdges: r,
    width: o,
    height: i,
    fitView: a,
    minZoom: s,
    maxZoom: l,
    fitViewOptions: u,
    nodeOrigin: d,
    nodeExtent: c,
    zIndexMode: f
  }));
  return _.jsx(Z_, { value: y, children: _.jsx(SE, { children: m }) });
}
function $2({ children: e, nodes: t, edges: n, defaultNodes: r, defaultEdges: o, width: i, height: s, fitView: l, fitViewOptions: u, minZoom: a, maxZoom: d, nodeOrigin: c, nodeExtent: f, zIndexMode: m }) {
  return F.useContext(Rs) ? _.jsx(_.Fragment, { children: e }) : _.jsx(dm, { initialNodes: t, initialEdges: n, defaultNodes: r, defaultEdges: o, initialWidth: i, initialHeight: s, fitView: l, initialFitViewOptions: u, initialMinZoom: a, initialMaxZoom: d, nodeOrigin: c, nodeExtent: f, zIndexMode: m, children: e });
}
const D2 = {
  width: "100%",
  height: "100%",
  overflow: "hidden",
  position: "relative",
  zIndex: 0
};
function R2({ nodes: e, edges: t, defaultNodes: n, defaultEdges: r, className: o, nodeTypes: i, edgeTypes: s, onNodeClick: l, onEdgeClick: u, onInit: a, onMove: d, onMoveStart: c, onMoveEnd: f, onConnect: m, onConnectStart: y, onConnectEnd: w, onClickConnectStart: x, onClickConnectEnd: p, onNodeMouseEnter: g, onNodeMouseMove: h, onNodeMouseLeave: v, onNodeContextMenu: S, onNodeDoubleClick: E, onNodeDragStart: M, onNodeDrag: P, onNodeDragStop: T, onNodesDelete: j, onEdgesDelete: I, onDelete: D, onSelectionChange: H, onSelectionDragStart: k, onSelectionDrag: A, onSelectionDragStop: z, onSelectionContextMenu: $, onSelectionStart: N, onSelectionEnd: C, onBeforeDelete: L, connectionMode: O, connectionLineType: R = Qt.Bezier, connectionLineStyle: W, connectionLineComponent: U, connectionLineContainerStyle: X, deleteKeyCode: Q = "Backspace", selectionKeyCode: K = "Shift", selectionOnDrag: B = !1, selectionMode: G = ko.Full, panActivationKeyCode: ee = "Space", multiSelectionKeyCode: J = Mo() ? "Meta" : "Control", zoomActivationKeyCode: q = Mo() ? "Meta" : "Control", snapToGrid: Z, snapGrid: ie, onlyRenderVisibleElements: ue = !1, selectNodesOnDrag: oe, nodesDraggable: Pe, autoPanOnNodeFocus: Ft, nodesConnectable: kt, nodesFocusable: fn, nodeOrigin: kr = Hg, edgesFocusable: Nr, edgesReconnectable: Cr, elementsSelectable: Vs = !0, defaultViewport: Ho = cE, minZoom: $n = 0.5, maxZoom: Mr = 2, translateExtent: Xa = Eo, preventScrolling: ym = !0, nodeExtent: Bs, defaultMarkerColor: vm = "#b1b1b7", zoomOnScroll: wm = !0, zoomOnPinch: xm = !0, panOnScroll: Sm = !1, panOnScrollSpeed: _m = 0.5, panOnScrollMode: Em = _n.Free, zoomOnDoubleClick: km = !0, panOnDrag: Nm = !0, onPaneClick: Cm, onPaneMouseEnter: Mm, onPaneMouseMove: Pm, onPaneMouseLeave: zm, onPaneScroll: Im, onPaneContextMenu: Tm, paneClickDistance: Lm = 1, nodeClickDistance: Am = 0, children: $m, onReconnect: Dm, onReconnectStart: Rm, onReconnectEnd: jm, onEdgeContextMenu: Om, onEdgeDoubleClick: Fm, onEdgeMouseEnter: Hm, onEdgeMouseMove: Vm, onEdgeMouseLeave: Bm, reconnectRadius: Um = 10, onNodesChange: Wm, onEdgesChange: Ym, noDragClassName: Xm = "nodrag", noWheelClassName: Qm = "nowheel", noPanClassName: Qa = "nopan", fitView: ba, fitViewOptions: Ka, connectOnClick: bm, attributionPosition: Km, proOptions: Gm, defaultEdgeOptions: Zm, elevateNodesOnSelect: qm = !0, elevateEdgesOnSelect: Jm = !1, disableKeyboardA11y: Ga = !1, autoPanOnConnect: e0, autoPanOnNodeDrag: t0, autoPanSpeed: n0, connectionRadius: r0, isValidConnection: o0, onError: i0, style: s0, id: Za, nodeDragThreshold: l0, connectionDragThreshold: u0, viewport: a0, onViewportChange: c0, width: f0, height: d0, colorMode: h0 = "light", debug: p0, onScroll: Vo, ariaLabelConfig: g0, zIndexMode: qa = "basic", ...m0 }, y0) {
  const Us = Za || "1", v0 = pE(h0), w0 = F.useCallback((Ja) => {
    Ja.currentTarget.scrollTo({ top: 0, left: 0, behavior: "instant" }), Vo == null || Vo(Ja);
  }, [Vo]);
  return _.jsx("div", { "data-testid": "rf__wrapper", ...m0, onScroll: w0, style: { ...s0, ...D2 }, ref: y0, className: xe(["react-flow", o, v0]), id: Za, role: "application", children: _.jsxs($2, { nodes: e, edges: t, width: f0, height: d0, fitView: ba, fitViewOptions: Ka, minZoom: $n, maxZoom: Mr, nodeOrigin: kr, nodeExtent: Bs, zIndexMode: qa, children: [_.jsx(hE, { nodes: e, edges: t, defaultNodes: n, defaultEdges: r, onConnect: m, onConnectStart: y, onConnectEnd: w, onClickConnectStart: x, onClickConnectEnd: p, nodesDraggable: Pe, autoPanOnNodeFocus: Ft, nodesConnectable: kt, nodesFocusable: fn, edgesFocusable: Nr, edgesReconnectable: Cr, elementsSelectable: Vs, elevateNodesOnSelect: qm, elevateEdgesOnSelect: Jm, minZoom: $n, maxZoom: Mr, nodeExtent: Bs, onNodesChange: Wm, onEdgesChange: Ym, snapToGrid: Z, snapGrid: ie, connectionMode: O, translateExtent: Xa, connectOnClick: bm, defaultEdgeOptions: Zm, fitView: ba, fitViewOptions: Ka, onNodesDelete: j, onEdgesDelete: I, onDelete: D, onNodeDragStart: M, onNodeDrag: P, onNodeDragStop: T, onSelectionDrag: A, onSelectionDragStart: k, onSelectionDragStop: z, onMove: d, onMoveStart: c, onMoveEnd: f, noPanClassName: Qa, nodeOrigin: kr, rfId: Us, autoPanOnConnect: e0, autoPanOnNodeDrag: t0, autoPanSpeed: n0, onError: i0, connectionRadius: r0, isValidConnection: o0, selectNodesOnDrag: oe, nodeDragThreshold: l0, connectionDragThreshold: u0, onBeforeDelete: L, debug: p0, ariaLabelConfig: g0, zIndexMode: qa }), _.jsx(L2, { onInit: a, onNodeClick: l, onEdgeClick: u, onNodeMouseEnter: g, onNodeMouseMove: h, onNodeMouseLeave: v, onNodeContextMenu: S, onNodeDoubleClick: E, nodeTypes: i, edgeTypes: s, connectionLineType: R, connectionLineStyle: W, connectionLineComponent: U, connectionLineContainerStyle: X, selectionKeyCode: K, selectionOnDrag: B, selectionMode: G, deleteKeyCode: Q, multiSelectionKeyCode: J, panActivationKeyCode: ee, zoomActivationKeyCode: q, onlyRenderVisibleElements: ue, defaultViewport: Ho, translateExtent: Xa, minZoom: $n, maxZoom: Mr, preventScrolling: ym, zoomOnScroll: wm, zoomOnPinch: xm, zoomOnDoubleClick: km, panOnScroll: Sm, panOnScrollSpeed: _m, panOnScrollMode: Em, panOnDrag: Nm, onPaneClick: Cm, onPaneMouseEnter: Mm, onPaneMouseMove: Pm, onPaneMouseLeave: zm, onPaneScroll: Im, onPaneContextMenu: Tm, paneClickDistance: Lm, nodeClickDistance: Am, onSelectionContextMenu: $, onSelectionStart: N, onSelectionEnd: C, onReconnect: Dm, onReconnectStart: Rm, onReconnectEnd: jm, onEdgeContextMenu: Om, onEdgeDoubleClick: Fm, onEdgeMouseEnter: Hm, onEdgeMouseMove: Vm, onEdgeMouseLeave: Bm, reconnectRadius: Um, defaultMarkerColor: vm, noDragClassName: Xm, noWheelClassName: Qm, noPanClassName: Qa, rfId: Us, disableKeyboardA11y: Ga, nodeExtent: Bs, viewport: a0, onViewportChange: c0 }), _.jsx(aE, { onSelectionChange: H }), $m, _.jsx(oE, { proOptions: Gm, position: Km }), _.jsx(rE, { rfId: Us, disableKeyboardA11y: Ga })] }) });
}
var j2 = Bg(R2);
function O2({ dimensions: e, lineWidth: t, variant: n, className: r }) {
  return _.jsx("path", { strokeWidth: t, d: `M${e[0] / 2} 0 V${e[1]} M0 ${e[1] / 2} H${e[0]}`, className: xe(["react-flow__background-pattern", n, r]) });
}
function F2({ radius: e, className: t }) {
  return _.jsx("circle", { cx: e, cy: e, r: e, className: xe(["react-flow__background-pattern", "dots", t]) });
}
var on;
(function(e) {
  e.Lines = "lines", e.Dots = "dots", e.Cross = "cross";
})(on || (on = {}));
const H2 = {
  [on.Dots]: 1,
  [on.Lines]: 1,
  [on.Cross]: 6
}, V2 = (e) => ({ transform: e.transform, patternId: `pattern-${e.rfId}` });
function hm({
  id: e,
  variant: t = on.Dots,
  // only used for dots and cross
  gap: n = 20,
  // only used for lines and cross
  size: r,
  lineWidth: o = 1,
  offset: i = 0,
  color: s,
  bgColor: l,
  style: u,
  className: a,
  patternClassName: d
}) {
  const c = F.useRef(null), { transform: f, patternId: m } = ne(V2, de), y = r || H2[t], w = t === on.Dots, x = t === on.Cross, p = Array.isArray(n) ? n : [n, n], g = [p[0] * f[2] || 1, p[1] * f[2] || 1], h = y * f[2], v = Array.isArray(i) ? i : [i, i], S = x ? [h, h] : g, E = [
    v[0] * f[2] || 1 + S[0] / 2,
    v[1] * f[2] || 1 + S[1] / 2
  ], M = `${m}${e || ""}`;
  return _.jsxs("svg", { className: xe(["react-flow__background", a]), style: {
    ...u,
    ...Fs,
    "--xy-background-color-props": l,
    "--xy-background-pattern-color-props": s
  }, ref: c, "data-testid": "rf__background", children: [_.jsx("pattern", { id: M, x: f[0] % g[0], y: f[1] % g[1], width: g[0], height: g[1], patternUnits: "userSpaceOnUse", patternTransform: `translate(-${E[0]},-${E[1]})`, children: w ? _.jsx(F2, { radius: h / 2, className: d }) : _.jsx(O2, { dimensions: S, lineWidth: o, variant: t, className: d }) }), _.jsx("rect", { x: "0", y: "0", width: "100%", height: "100%", fill: `url(#${M})` })] });
}
hm.displayName = "Background";
const B2 = F.memo(hm);
function U2() {
  return _.jsx("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 32 32", children: _.jsx("path", { d: "M32 18.133H18.133V32h-4.266V18.133H0v-4.266h13.867V0h4.266v13.867H32z" }) });
}
function W2() {
  return _.jsx("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 32 5", children: _.jsx("path", { d: "M0 0h32v4.2H0z" }) });
}
function Y2() {
  return _.jsx("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 32 30", children: _.jsx("path", { d: "M3.692 4.63c0-.53.4-.938.939-.938h5.215V0H4.708C2.13 0 0 2.054 0 4.63v5.216h3.692V4.631zM27.354 0h-5.2v3.692h5.17c.53 0 .984.4.984.939v5.215H32V4.631A4.624 4.624 0 0027.354 0zm.954 24.83c0 .532-.4.94-.939.94h-5.215v3.768h5.215c2.577 0 4.631-2.13 4.631-4.707v-5.139h-3.692v5.139zm-23.677.94c-.531 0-.939-.4-.939-.94v-5.138H0v5.139c0 2.577 2.13 4.707 4.708 4.707h5.138V25.77H4.631z" }) });
}
function X2() {
  return _.jsx("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 25 32", children: _.jsx("path", { d: "M21.333 10.667H19.81V7.619C19.81 3.429 16.38 0 12.19 0 8 0 4.571 3.429 4.571 7.619v3.048H3.048A3.056 3.056 0 000 13.714v15.238A3.056 3.056 0 003.048 32h18.285a3.056 3.056 0 003.048-3.048V13.714a3.056 3.056 0 00-3.048-3.047zM12.19 24.533a3.056 3.056 0 01-3.047-3.047 3.056 3.056 0 013.047-3.048 3.056 3.056 0 013.048 3.048 3.056 3.056 0 01-3.048 3.047zm4.724-13.866H7.467V7.619c0-2.59 2.133-4.724 4.723-4.724 2.591 0 4.724 2.133 4.724 4.724v3.048z" }) });
}
function Q2() {
  return _.jsx("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 25 32", children: _.jsx("path", { d: "M21.333 10.667H19.81V7.619C19.81 3.429 16.38 0 12.19 0c-4.114 1.828-1.37 2.133.305 2.438 1.676.305 4.42 2.59 4.42 5.181v3.048H3.047A3.056 3.056 0 000 13.714v15.238A3.056 3.056 0 003.048 32h18.285a3.056 3.056 0 003.048-3.048V13.714a3.056 3.056 0 00-3.048-3.047zM12.19 24.533a3.056 3.056 0 01-3.047-3.047 3.056 3.056 0 013.047-3.048 3.056 3.056 0 013.048 3.048 3.056 3.056 0 01-3.048 3.047z" }) });
}
function hi({ children: e, className: t, ...n }) {
  return _.jsx("button", { type: "button", className: xe(["react-flow__controls-button", t]), ...n, children: e });
}
const b2 = (e) => ({
  isInteractive: e.nodesDraggable || e.nodesConnectable || e.elementsSelectable,
  minZoomReached: e.transform[2] <= e.minZoom,
  maxZoomReached: e.transform[2] >= e.maxZoom,
  ariaLabelConfig: e.ariaLabelConfig
});
function pm({ style: e, showZoom: t = !0, showFitView: n = !0, showInteractive: r = !0, fitViewOptions: o, onZoomIn: i, onZoomOut: s, onFitView: l, onInteractiveChange: u, className: a, children: d, position: c = "bottom-left", orientation: f = "vertical", "aria-label": m }) {
  const y = he(), { isInteractive: w, minZoomReached: x, maxZoomReached: p, ariaLabelConfig: g } = ne(b2, de), { zoomIn: h, zoomOut: v, fitView: S } = Os(), E = () => {
    h(), i == null || i();
  }, M = () => {
    v(), s == null || s();
  }, P = () => {
    S(o), l == null || l();
  }, T = () => {
    y.setState({
      nodesDraggable: !w,
      nodesConnectable: !w,
      elementsSelectable: !w
    }), u == null || u(!w);
  }, j = f === "horizontal" ? "horizontal" : "vertical";
  return _.jsxs(js, { className: xe(["react-flow__controls", j, a]), position: c, style: e, "data-testid": "rf__controls", "aria-label": m ?? g["controls.ariaLabel"], children: [t && _.jsxs(_.Fragment, { children: [_.jsx(hi, { onClick: E, className: "react-flow__controls-zoomin", title: g["controls.zoomIn.ariaLabel"], "aria-label": g["controls.zoomIn.ariaLabel"], disabled: p, children: _.jsx(U2, {}) }), _.jsx(hi, { onClick: M, className: "react-flow__controls-zoomout", title: g["controls.zoomOut.ariaLabel"], "aria-label": g["controls.zoomOut.ariaLabel"], disabled: x, children: _.jsx(W2, {}) })] }), n && _.jsx(hi, { className: "react-flow__controls-fitview", onClick: P, title: g["controls.fitView.ariaLabel"], "aria-label": g["controls.fitView.ariaLabel"], children: _.jsx(Y2, {}) }), r && _.jsx(hi, { className: "react-flow__controls-interactive", onClick: T, title: g["controls.interactive.ariaLabel"], "aria-label": g["controls.interactive.ariaLabel"], children: w ? _.jsx(Q2, {}) : _.jsx(X2, {}) }), d] });
}
pm.displayName = "Controls";
const K2 = F.memo(pm);
function G2({ id: e, x: t, y: n, width: r, height: o, style: i, color: s, strokeColor: l, strokeWidth: u, className: a, borderRadius: d, shapeRendering: c, selected: f, onClick: m }) {
  const { background: y, backgroundColor: w } = i || {}, x = s || y || w;
  return _.jsx("rect", { className: xe(["react-flow__minimap-node", { selected: f }, a]), x: t, y: n, rx: d, ry: d, width: r, height: o, style: {
    fill: x,
    stroke: l,
    strokeWidth: u
  }, shapeRendering: c, onClick: m ? (p) => m(p, e) : void 0 });
}
const Z2 = F.memo(G2), q2 = (e) => e.nodes.map((t) => t.id), Nl = (e) => e instanceof Function ? e : () => e;
function J2({
  nodeStrokeColor: e,
  nodeColor: t,
  nodeClassName: n = "",
  nodeBorderRadius: r = 5,
  nodeStrokeWidth: o,
  /*
   * We need to rename the prop to be `CapitalCase` so that JSX will render it as
   * a component properly.
   */
  nodeComponent: i = Z2,
  onClick: s
}) {
  const l = ne(q2, de), u = Nl(t), a = Nl(e), d = Nl(n), c = typeof window > "u" || window.chrome ? "crispEdges" : "geometricPrecision";
  return _.jsx(_.Fragment, { children: l.map((f) => (
    /*
     * The split of responsibilities between MiniMapNodes and
     * NodeComponentWrapper may appear weird. However, it’s designed to
     * minimize the cost of updates when individual nodes change.
     *
     * For more details, see a similar commit in `NodeRenderer/index.tsx`.
     */
    _.jsx(tk, { id: f, nodeColorFunc: u, nodeStrokeColorFunc: a, nodeClassNameFunc: d, nodeBorderRadius: r, nodeStrokeWidth: o, NodeComponent: i, onClick: s, shapeRendering: c }, f)
  )) });
}
function ek({ id: e, nodeColorFunc: t, nodeStrokeColorFunc: n, nodeClassNameFunc: r, nodeBorderRadius: o, nodeStrokeWidth: i, shapeRendering: s, NodeComponent: l, onClick: u }) {
  const { node: a, x: d, y: c, width: f, height: m } = ne((y) => {
    const w = y.nodeLookup.get(e);
    if (!w)
      return { node: void 0, x: 0, y: 0, width: 0, height: 0 };
    const x = w.internals.userNode, { x: p, y: g } = w.internals.positionAbsolute, { width: h, height: v } = Ot(x);
    return {
      node: x,
      x: p,
      y: g,
      width: h,
      height: v
    };
  }, de);
  return !a || a.hidden || !dg(a) ? null : _.jsx(l, { x: d, y: c, width: f, height: m, style: a.style, selected: !!a.selected, className: r(a), color: t(a), borderRadius: o, strokeColor: n(a), strokeWidth: i, shapeRendering: s, onClick: u, id: a.id });
}
const tk = F.memo(ek);
var nk = F.memo(J2);
const rk = 200, ok = 150, ik = (e) => !e.hidden, sk = (e) => {
  const t = {
    x: -e.transform[0] / e.transform[2],
    y: -e.transform[1] / e.transform[2],
    width: e.width / e.transform[2],
    height: e.height / e.transform[2]
  };
  return {
    viewBB: t,
    boundingRect: e.nodeLookup.size > 0 ? fg(jo(e.nodeLookup, { filter: ik }), t) : t,
    rfId: e.rfId,
    panZoom: e.panZoom,
    translateExtent: e.translateExtent,
    flowWidth: e.width,
    flowHeight: e.height,
    ariaLabelConfig: e.ariaLabelConfig
  };
}, lk = "react-flow__minimap-desc";
function gm({
  style: e,
  className: t,
  nodeStrokeColor: n,
  nodeColor: r,
  nodeClassName: o = "",
  nodeBorderRadius: i = 5,
  nodeStrokeWidth: s,
  /*
   * We need to rename the prop to be `CapitalCase` so that JSX will render it as
   * a component properly.
   */
  nodeComponent: l,
  bgColor: u,
  maskColor: a,
  maskStrokeColor: d,
  maskStrokeWidth: c,
  position: f = "bottom-right",
  onClick: m,
  onNodeClick: y,
  pannable: w = !1,
  zoomable: x = !1,
  ariaLabel: p,
  inversePan: g,
  zoomStep: h = 1,
  offsetScale: v = 5
}) {
  const S = he(), E = F.useRef(null), { boundingRect: M, viewBB: P, rfId: T, panZoom: j, translateExtent: I, flowWidth: D, flowHeight: H, ariaLabelConfig: k } = ne(sk, de), A = (e == null ? void 0 : e.width) ?? rk, z = (e == null ? void 0 : e.height) ?? ok, $ = M.width / A, N = M.height / z, C = Math.max($, N), L = C * A, O = C * z, R = v * C, W = M.x - (L - M.width) / 2 - R, U = M.y - (O - M.height) / 2 - R, X = L + R * 2, Q = O + R * 2, K = `${lk}-${T}`, B = F.useRef(0), G = F.useRef();
  B.current = C, F.useEffect(() => {
    if (E.current && j)
      return G.current = c_({
        domNode: E.current,
        panZoom: j,
        getTransform: () => S.getState().transform,
        getViewScale: () => B.current
      }), () => {
        var Z;
        (Z = G.current) == null || Z.destroy();
      };
  }, [j]), F.useEffect(() => {
    var Z;
    (Z = G.current) == null || Z.update({
      translateExtent: I,
      width: D,
      height: H,
      inversePan: g,
      pannable: w,
      zoomStep: h,
      zoomable: x
    });
  }, [w, x, g, h, I, D, H]);
  const ee = m ? (Z) => {
    var oe;
    const [ie, ue] = ((oe = G.current) == null ? void 0 : oe.pointer(Z)) || [0, 0];
    m(Z, { x: ie, y: ue });
  } : void 0, J = y ? F.useCallback((Z, ie) => {
    const ue = S.getState().nodeLookup.get(ie).internals.userNode;
    y(Z, ue);
  }, []) : void 0, q = p ?? k["minimap.ariaLabel"];
  return _.jsx(js, { position: f, style: {
    ...e,
    "--xy-minimap-background-color-props": typeof u == "string" ? u : void 0,
    "--xy-minimap-mask-background-color-props": typeof a == "string" ? a : void 0,
    "--xy-minimap-mask-stroke-color-props": typeof d == "string" ? d : void 0,
    "--xy-minimap-mask-stroke-width-props": typeof c == "number" ? c * C : void 0,
    "--xy-minimap-node-background-color-props": typeof r == "string" ? r : void 0,
    "--xy-minimap-node-stroke-color-props": typeof n == "string" ? n : void 0,
    "--xy-minimap-node-stroke-width-props": typeof s == "number" ? s : void 0
  }, className: xe(["react-flow__minimap", t]), "data-testid": "rf__minimap", children: _.jsxs("svg", { width: A, height: z, viewBox: `${W} ${U} ${X} ${Q}`, className: "react-flow__minimap-svg", role: "img", "aria-labelledby": K, ref: E, onClick: ee, children: [q && _.jsx("title", { id: K, children: q }), _.jsx(nk, { onClick: J, nodeColor: r, nodeStrokeColor: n, nodeBorderRadius: i, nodeClassName: o, nodeStrokeWidth: s, nodeComponent: l }), _.jsx("path", { className: "react-flow__minimap-mask", d: `M${W - R},${U - R}h${X + R * 2}v${Q + R * 2}h${-X - R * 2}z
        M${P.x},${P.y}h${P.width}v${P.height}h${-P.width}z`, fillRule: "evenodd", pointerEvents: "none" })] }) });
}
gm.displayName = "MiniMap";
F.memo(gm);
const uk = (e) => (t) => e ? `${Math.max(1 / t.transform[2], 1)}` : void 0, ak = {
  [vr.Line]: "right",
  [vr.Handle]: "bottom-right"
};
function ck({ nodeId: e, position: t, variant: n = vr.Handle, className: r, style: o = void 0, children: i, color: s, minWidth: l = 10, minHeight: u = 10, maxWidth: a = Number.MAX_VALUE, maxHeight: d = Number.MAX_VALUE, keepAspectRatio: c = !1, resizeDirection: f, autoScale: m = !0, shouldResize: y, onResizeStart: w, onResize: x, onResizeEnd: p }) {
  const g = Xg(), h = typeof e == "string" ? e : g, v = he(), S = F.useRef(null), E = n === vr.Handle, M = ne(F.useCallback(uk(E && m), [E, m]), de), P = F.useRef(null), T = t ?? ak[n];
  F.useEffect(() => {
    if (!(!S.current || !h))
      return P.current || (P.current = k_({
        domNode: S.current,
        nodeId: h,
        getStoreItems: () => {
          const { nodeLookup: I, transform: D, snapGrid: H, snapToGrid: k, nodeOrigin: A, domNode: z } = v.getState();
          return {
            nodeLookup: I,
            transform: D,
            snapGrid: H,
            snapToGrid: k,
            nodeOrigin: A,
            paneDomNode: z
          };
        },
        onChange: (I, D) => {
          const { triggerNodeChanges: H, nodeLookup: k, parentLookup: A, nodeOrigin: z } = v.getState(), $ = [], N = { x: I.x, y: I.y }, C = k.get(h);
          if (C && C.expandParent && C.parentId) {
            const L = C.origin ?? z, O = I.width ?? C.measured.width ?? 0, R = I.height ?? C.measured.height ?? 0, W = {
              id: C.id,
              parentId: C.parentId,
              rect: {
                width: O,
                height: R,
                ...hg({
                  x: I.x ?? C.position.x,
                  y: I.y ?? C.position.y
                }, { width: O, height: R }, C.parentId, k, L)
              }
            }, U = Ua([W], k, A, z);
            $.push(...U), N.x = I.x ? Math.max(L[0] * O, I.x) : void 0, N.y = I.y ? Math.max(L[1] * R, I.y) : void 0;
          }
          if (N.x !== void 0 && N.y !== void 0) {
            const L = {
              id: h,
              type: "position",
              position: { ...N }
            };
            $.push(L);
          }
          if (I.width !== void 0 && I.height !== void 0) {
            const O = {
              id: h,
              type: "dimensions",
              resizing: !0,
              setAttributes: f ? f === "horizontal" ? "width" : "height" : !0,
              dimensions: {
                width: I.width,
                height: I.height
              }
            };
            $.push(O);
          }
          for (const L of D) {
            const O = {
              ...L,
              type: "position"
            };
            $.push(O);
          }
          H($);
        },
        onEnd: ({ width: I, height: D }) => {
          const H = {
            id: h,
            type: "dimensions",
            resizing: !1,
            dimensions: {
              width: I,
              height: D
            }
          };
          v.getState().triggerNodeChanges([H]);
        }
      })), P.current.update({
        controlPosition: T,
        boundaries: {
          minWidth: l,
          minHeight: u,
          maxWidth: a,
          maxHeight: d
        },
        keepAspectRatio: c,
        resizeDirection: f,
        onResizeStart: w,
        onResize: x,
        onResizeEnd: p,
        shouldResize: y
      }), () => {
        var I;
        (I = P.current) == null || I.destroy();
      };
  }, [
    T,
    l,
    u,
    a,
    d,
    c,
    w,
    x,
    p,
    y
  ]);
  const j = T.split("-");
  return _.jsx("div", { className: xe(["react-flow__resize-control", "nodrag", ...j, n, r]), ref: S, style: {
    ...o,
    scale: M,
    ...s && { [E ? "backgroundColor" : "borderColor"]: s }
  }, children: i });
}
F.memo(ck);
const dd = /* @__PURE__ */ new WeakMap(), fk = {
  teamOrgNode: pk
};
function dk({ payload: e }) {
  return e ? /* @__PURE__ */ _.jsx(dm, { children: /* @__PURE__ */ _.jsx(hk, { payload: e }) }) : null;
}
function hk({ payload: e }) {
  const t = Os(), n = F.useMemo(() => e.nodes, [e]), r = F.useMemo(() => e.edges, [e]);
  return F.useEffect(() => {
    const o = window.requestAnimationFrame(() => {
      t.fitView({ padding: 0.18, duration: 180 });
    });
    return () => window.cancelAnimationFrame(o);
  }, [e.fitKey, t]), /* @__PURE__ */ _.jsx("div", { className: "team-org-flow-canvas", "data-team-org-flow-canvas": !0, children: /* @__PURE__ */ _.jsxs(
    j2,
    {
      nodes: n,
      edges: r,
      nodeTypes: fk,
      nodesDraggable: !1,
      nodesConnectable: !1,
      elementsSelectable: !0,
      selectNodesOnDrag: !1,
      panOnScroll: !0,
      zoomOnScroll: !0,
      zoomOnPinch: !0,
      fitView: !0,
      minZoom: 0.25,
      maxZoom: 1.5,
      defaultEdgeOptions: {
        type: "smoothstep",
        markerEnd: { type: No.ArrowClosed }
      },
      proOptions: { hideAttribution: !0 },
      children: [
        /* @__PURE__ */ _.jsx(B2, { gap: 24, size: 1 }),
        /* @__PURE__ */ _.jsx(K2, { position: "bottom-left", showInteractive: !1 })
      ]
    }
  ) });
}
function pk({ data: e }) {
  let t;
  return e.kind === "agent" ? t = /* @__PURE__ */ _.jsx(gk, { data: e }) : e.kind === "team" ? t = /* @__PURE__ */ _.jsx(mk, { data: e }) : e.kind === "governance" ? t = /* @__PURE__ */ _.jsx(yk, { data: e }) : t = /* @__PURE__ */ _.jsx(vk, { data: e }), /* @__PURE__ */ _.jsxs("div", { className: `team-org-flow-node team-org-flow-node--${e.kind}`, children: [
    /* @__PURE__ */ _.jsx(
      xr,
      {
        className: "team-org-flow-handle team-org-flow-handle--target",
        type: "target",
        position: b.Top,
        isConnectable: !1
      }
    ),
    t,
    /* @__PURE__ */ _.jsx(
      xr,
      {
        className: "team-org-flow-handle team-org-flow-handle--source",
        type: "source",
        position: b.Bottom,
        isConnectable: !1
      }
    )
  ] });
}
function gk({ data: e }) {
  const t = () => {
    window.dispatchEvent(
      new CustomEvent("team-org-flow:edit-agent", {
        detail: { agentId: e.agentId }
      })
    );
  };
  return /* @__PURE__ */ _.jsxs(
    "article",
    {
      className: e.cardClassName,
      tabIndex: 0,
      role: "button",
      "aria-label": `Edit ${e.label}`,
      onClick: t,
      onKeyDown: (n) => {
        n.key !== "Enter" && n.key !== " " || (n.preventDefault(), t());
      },
      children: [
        /* @__PURE__ */ _.jsxs("div", { className: "hierarchy-node-summary", children: [
          /* @__PURE__ */ _.jsxs("div", { className: "hierarchy-node-title", children: [
            /* @__PURE__ */ _.jsx(mm, { avatar: e.avatar }),
            /* @__PURE__ */ _.jsx("strong", { children: e.label }),
            e.idLabel ? /* @__PURE__ */ _.jsx("span", { className: "hierarchy-node-id", children: e.idLabel }) : null
          ] }),
          /* @__PURE__ */ _.jsxs("div", { className: "hierarchy-node-subtitle", children: [
            /* @__PURE__ */ _.jsx("span", { className: "hierarchy-node-role", children: e.role }),
            /* @__PURE__ */ _.jsx("span", { className: `state-pill ${e.status}`, children: e.status }),
            /* @__PURE__ */ _.jsx(wk, { awake: e.awake }),
            /* @__PURE__ */ _.jsx(xk, { indicators: e.inboxIndicators })
          ] })
        ] }),
        /* @__PURE__ */ _.jsxs("div", { className: "org-node-work", children: [
          /* @__PURE__ */ _.jsxs("div", { className: "org-node-work-line org-node-work-line--now", children: [
            /* @__PURE__ */ _.jsx("span", { children: "Now" }),
            /* @__PURE__ */ _.jsx("strong", { children: e.work.primary })
          ] }),
          /* @__PURE__ */ _.jsxs("div", { className: "org-node-work-line org-node-work-line--waiting", children: [
            /* @__PURE__ */ _.jsx("span", { children: "Waiting" }),
            /* @__PURE__ */ _.jsx("strong", { children: e.work.waiting })
          ] })
        ] }),
        /* @__PURE__ */ _.jsx("div", { className: "hierarchy-node-details org-node-hover-panel", children: e.detailLines.map((n, r) => /* @__PURE__ */ _.jsx("div", { className: "meta-line", children: n }, `${n}-${r}`)) })
      ]
    }
  );
}
function mk({ data: e }) {
  return /* @__PURE__ */ _.jsxs("article", { className: "org-focus-team org-flow-team-node", children: [
    /* @__PURE__ */ _.jsxs("div", { className: "org-flow-team-header", children: [
      /* @__PURE__ */ _.jsxs("div", { className: "org-flow-team-title", children: [
        /* @__PURE__ */ _.jsx("p", { className: "section-label", children: e.teamId }),
        /* @__PURE__ */ _.jsx("h3", { children: e.name })
      ] }),
      /* @__PURE__ */ _.jsxs("div", { className: "org-flow-node-pills org-focus-team-status", children: [
        e.archived ? /* @__PURE__ */ _.jsx("span", { className: "state-pill archived", children: "archived" }) : null,
        e.costBlocked ? /* @__PURE__ */ _.jsx("span", { className: "state-pill warning team-cost-blocked-pill", children: "cost blocked" }) : null,
        /* @__PURE__ */ _.jsxs("span", { className: "state-pill info", children: [
          e.awakeCount,
          " awake"
        ] }),
        /* @__PURE__ */ _.jsxs("span", { className: `state-pill ${e.attentionCount ? "warning" : "accepted"}`, children: [
          e.attentionCount,
          " attention"
        ] })
      ] })
    ] }),
    /* @__PURE__ */ _.jsx(Au, { html: e.costHtml }),
    /* @__PURE__ */ _.jsx(Au, { html: e.costBlockedNoticeHtml })
  ] });
}
function yk({ data: e }) {
  return /* @__PURE__ */ _.jsxs("article", { className: "org-flow-governance-node governance-item", "data-governance-group-id": e.groupId, children: [
    /* @__PURE__ */ _.jsxs("div", { className: "org-flow-governance-header", children: [
      /* @__PURE__ */ _.jsxs("div", { className: "org-flow-governance-title", children: [
        /* @__PURE__ */ _.jsx("p", { className: "section-label", children: "Governance" }),
        /* @__PURE__ */ _.jsx("h3", { children: e.name })
      ] }),
      /* @__PURE__ */ _.jsxs("div", { className: "org-flow-node-pills", children: [
        e.archived ? /* @__PURE__ */ _.jsx("span", { className: "state-pill blocked", children: "archived" }) : null,
        e.costAttention ? /* @__PURE__ */ _.jsx("span", { className: "state-pill warning", children: "cost attention" }) : null,
        /* @__PURE__ */ _.jsxs("span", { className: "state-pill neutral", children: [
          e.memberTeamCount,
          " team",
          e.memberTeamCount === 1 ? "" : "s"
        ] }),
        /* @__PURE__ */ _.jsxs("span", { className: `state-pill ${e.pendingAgendaCount ? "warning" : "accepted"}`, children: [
          e.pendingAgendaCount,
          " agenda"
        ] })
      ] }),
      /* @__PURE__ */ _.jsxs("div", { className: "org-flow-governance-manager", children: [
        e.managerAvatar ? /* @__PURE__ */ _.jsx(mm, { avatar: e.managerAvatar, extraClass: "agent-avatar-small" }) : null,
        /* @__PURE__ */ _.jsx("span", { children: e.managerLabel })
      ] })
    ] }),
    /* @__PURE__ */ _.jsx("div", { className: "meta-line", children: e.groupId }),
    /* @__PURE__ */ _.jsx(Au, { html: e.costHtml })
  ] });
}
function vk({ data: e }) {
  return /* @__PURE__ */ _.jsxs("article", { className: "hierarchy-node-card hierarchy-node-card--warning org-flow-warning-node", tabIndex: 0, children: [
    /* @__PURE__ */ _.jsxs("div", { className: "hierarchy-node-summary", children: [
      /* @__PURE__ */ _.jsx("div", { className: "hierarchy-node-title", children: /* @__PURE__ */ _.jsx("strong", { children: e.label }) }),
      /* @__PURE__ */ _.jsx("div", { className: "hierarchy-node-subtitle", children: /* @__PURE__ */ _.jsx("span", { className: "state-pill warning", children: "warning" }) })
    ] }),
    e.detailLines.length ? /* @__PURE__ */ _.jsx("div", { className: "hierarchy-node-details", children: e.detailLines.map((t, n) => /* @__PURE__ */ _.jsx("div", { className: "meta-line", children: t }, `${t}-${n}`)) }) : null
  ] });
}
function mm({
  avatar: e,
  extraClass: t = ""
}) {
  const n = ["agent-avatar", t].filter(Boolean).join(" ");
  return e.src ? /* @__PURE__ */ _.jsx("span", { className: n, children: /* @__PURE__ */ _.jsx("img", { src: e.src, alt: e.alt, loading: "lazy" }) }) : /* @__PURE__ */ _.jsx("span", { className: `${n} agent-avatar-fallback`, "aria-hidden": "true", children: e.initials });
}
function wk({ awake: e }) {
  return /* @__PURE__ */ _.jsxs("span", { className: `awake-indicator awake-indicator--${e.kind}`, title: e.detail, children: [
    /* @__PURE__ */ _.jsx("span", { className: "awake-dot", "aria-hidden": "true" }),
    e.label
  ] });
}
function xk({ indicators: e }) {
  return /* @__PURE__ */ _.jsx("span", { className: "agent-inbox-indicators", "aria-label": "Pending inbox summary", children: e.map((t) => /* @__PURE__ */ _.jsxs(
    "span",
    {
      className: [
        "agent-inbox-indicator",
        `agent-inbox-indicator--${t.itemType}`,
        t.count ? "agent-inbox-indicator--active" : "agent-inbox-indicator--empty"
      ].join(" "),
      "aria-label": `${t.label}: ${t.count}`,
      children: [
        /* @__PURE__ */ _.jsx(Sk, { direction: t.direction }),
        /* @__PURE__ */ _.jsx("strong", { children: t.count })
      ]
    },
    t.itemType
  )) });
}
function Sk({ direction: e }) {
  return /* @__PURE__ */ _.jsxs("svg", { className: `agent-inbox-icon agent-inbox-icon--${e}`, viewBox: "0 0 20 20", "aria-hidden": "true", focusable: "false", children: [
    e === "up" ? /* @__PURE__ */ _.jsxs(_.Fragment, { children: [
      /* @__PURE__ */ _.jsx("path", { d: "M10 8V3" }),
      /* @__PURE__ */ _.jsx("path", { d: "M7.3 5.7 10 3l2.7 2.7" })
    ] }) : /* @__PURE__ */ _.jsxs(_.Fragment, { children: [
      /* @__PURE__ */ _.jsx("path", { d: "M10 3v5" }),
      /* @__PURE__ */ _.jsx("path", { d: "M7.3 5.3 10 8l2.7-2.7" })
    ] }),
    /* @__PURE__ */ _.jsx("path", { d: "M5.5 8.2h9a2.2 2.2 0 0 1 2.2 2.2v2.6a2.2 2.2 0 0 1-2.2 2.2H9.3L5 18v-2.8a2.2 2.2 0 0 1-1.7-2.1v-2.7a2.2 2.2 0 0 1 2.2-2.2Z" })
  ] });
}
function Au({ html: e }) {
  return e ? /* @__PURE__ */ _.jsx("div", { dangerouslySetInnerHTML: { __html: e } }) : null;
}
function Ya(e) {
  const t = document.querySelector("#team-org-flow-root");
  if (!t) {
    window.__TEAM_ORG_FLOW_PAYLOAD__ = e;
    return;
  }
  let n = dd.get(t);
  n || (n = Cp(t), dd.set(t, n)), n.render(/* @__PURE__ */ _.jsx(dk, { payload: e }));
}
window.renderTeamOrgFlow = Ya;
window.addEventListener("team-org-flow:render", (e) => {
  Ya(e.detail ?? null);
});
window.__TEAM_ORG_FLOW_PAYLOAD__ && Ya(window.__TEAM_ORG_FLOW_PAYLOAD__);
