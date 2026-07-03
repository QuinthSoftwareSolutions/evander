function pd(e) {
  return e && e.__esModule && Object.prototype.hasOwnProperty.call(e, "default") ? e.default : e;
}
var gd = { exports: {} }, ds = {}, md = { exports: {} }, te = {};
/**
 * @license React
 * react.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var Io = Symbol.for("react.element"), _0 = Symbol.for("react.portal"), E0 = Symbol.for("react.fragment"), k0 = Symbol.for("react.strict_mode"), N0 = Symbol.for("react.profiler"), C0 = Symbol.for("react.provider"), M0 = Symbol.for("react.context"), P0 = Symbol.for("react.forward_ref"), I0 = Symbol.for("react.suspense"), z0 = Symbol.for("react.memo"), T0 = Symbol.for("react.lazy"), tc = Symbol.iterator;
function L0(e) {
  return e === null || typeof e != "object" ? null : (e = tc && e[tc] || e["@@iterator"], typeof e == "function" ? e : null);
}
var yd = { isMounted: function() {
  return !1;
}, enqueueForceUpdate: function() {
}, enqueueReplaceState: function() {
}, enqueueSetState: function() {
} }, vd = Object.assign, wd = {};
function Sr(e, t, n) {
  this.props = e, this.context = t, this.refs = wd, this.updater = n || yd;
}
Sr.prototype.isReactComponent = {};
Sr.prototype.setState = function(e, t) {
  if (typeof e != "object" && typeof e != "function" && e != null) throw Error("setState(...): takes an object of state variables to update or a function which returns an object of state variables.");
  this.updater.enqueueSetState(this, e, t, "setState");
};
Sr.prototype.forceUpdate = function(e) {
  this.updater.enqueueForceUpdate(this, e, "forceUpdate");
};
function xd() {
}
xd.prototype = Sr.prototype;
function Du(e, t, n) {
  this.props = e, this.context = t, this.refs = wd, this.updater = n || yd;
}
var Ru = Du.prototype = new xd();
Ru.constructor = Du;
vd(Ru, Sr.prototype);
Ru.isPureReactComponent = !0;
var nc = Array.isArray, Sd = Object.prototype.hasOwnProperty, ju = { current: null }, _d = { key: !0, ref: !0, __self: !0, __source: !0 };
function Ed(e, t, n) {
  var r, o = {}, i = null, s = null;
  if (t != null) for (r in t.ref !== void 0 && (s = t.ref), t.key !== void 0 && (i = "" + t.key), t) Sd.call(t, r) && !_d.hasOwnProperty(r) && (o[r] = t[r]);
  var l = arguments.length - 2;
  if (l === 1) o.children = n;
  else if (1 < l) {
    for (var u = Array(l), a = 0; a < l; a++) u[a] = arguments[a + 2];
    o.children = u;
  }
  if (e && e.defaultProps) for (r in l = e.defaultProps, l) o[r] === void 0 && (o[r] = l[r]);
  return { $$typeof: Io, type: e, key: i, ref: s, props: o, _owner: ju.current };
}
function A0(e, t) {
  return { $$typeof: Io, type: e.type, key: t, ref: e.ref, props: e.props, _owner: e._owner };
}
function Ou(e) {
  return typeof e == "object" && e !== null && e.$$typeof === Io;
}
function $0(e) {
  var t = { "=": "=0", ":": "=2" };
  return "$" + e.replace(/[=:]/g, function(n) {
    return t[n];
  });
}
var rc = /\/+/g;
function Ys(e, t) {
  return typeof e == "object" && e !== null && e.key != null ? $0("" + e.key) : t.toString(36);
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
        case Io:
        case _0:
          s = !0;
      }
  }
  if (s) return s = e, o = o(s), e = r === "" ? "." + Ys(s, 0) : r, nc(o) ? (n = "", e != null && (n = e.replace(rc, "$&/") + "/"), pi(o, t, n, "", function(a) {
    return a;
  })) : o != null && (Ou(o) && (o = A0(o, n + (!o.key || s && s.key === o.key ? "" : ("" + o.key).replace(rc, "$&/") + "/") + e)), t.push(o)), 1;
  if (s = 0, r = r === "" ? "." : r + ":", nc(e)) for (var l = 0; l < e.length; l++) {
    i = e[l];
    var u = r + Ys(i, l);
    s += pi(i, t, n, u, o);
  }
  else if (u = L0(e), typeof u == "function") for (e = u.call(e), l = 0; !(i = e.next()).done; ) i = i.value, u = r + Ys(i, l++), s += pi(i, t, n, u, o);
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
function D0(e) {
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
var De = { current: null }, gi = { transition: null }, R0 = { ReactCurrentDispatcher: De, ReactCurrentBatchConfig: gi, ReactCurrentOwner: ju };
function kd() {
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
  if (!Ou(e)) throw Error("React.Children.only expected to receive a single React element child.");
  return e;
} };
te.Component = Sr;
te.Fragment = E0;
te.Profiler = N0;
te.PureComponent = Du;
te.StrictMode = k0;
te.Suspense = I0;
te.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED = R0;
te.act = kd;
te.cloneElement = function(e, t, n) {
  if (e == null) throw Error("React.cloneElement(...): The argument must be a React element, but you passed " + e + ".");
  var r = vd({}, e.props), o = e.key, i = e.ref, s = e._owner;
  if (t != null) {
    if (t.ref !== void 0 && (i = t.ref, s = ju.current), t.key !== void 0 && (o = "" + t.key), e.type && e.type.defaultProps) var l = e.type.defaultProps;
    for (u in t) Sd.call(t, u) && !_d.hasOwnProperty(u) && (r[u] = t[u] === void 0 && l !== void 0 ? l[u] : t[u]);
  }
  var u = arguments.length - 2;
  if (u === 1) r.children = n;
  else if (1 < u) {
    l = Array(u);
    for (var a = 0; a < u; a++) l[a] = arguments[a + 2];
    r.children = l;
  }
  return { $$typeof: Io, type: e.type, key: o, ref: i, props: r, _owner: s };
};
te.createContext = function(e) {
  return e = { $$typeof: M0, _currentValue: e, _currentValue2: e, _threadCount: 0, Provider: null, Consumer: null, _defaultValue: null, _globalName: null }, e.Provider = { $$typeof: C0, _context: e }, e.Consumer = e;
};
te.createElement = Ed;
te.createFactory = function(e) {
  var t = Ed.bind(null, e);
  return t.type = e, t;
};
te.createRef = function() {
  return { current: null };
};
te.forwardRef = function(e) {
  return { $$typeof: P0, render: e };
};
te.isValidElement = Ou;
te.lazy = function(e) {
  return { $$typeof: T0, _payload: { _status: -1, _result: e }, _init: D0 };
};
te.memo = function(e, t) {
  return { $$typeof: z0, type: e, compare: t === void 0 ? null : t };
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
te.unstable_act = kd;
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
md.exports = te;
var j = md.exports;
const j0 = /* @__PURE__ */ pd(j);
/**
 * @license React
 * react-jsx-runtime.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var O0 = j, F0 = Symbol.for("react.element"), H0 = Symbol.for("react.fragment"), V0 = Object.prototype.hasOwnProperty, B0 = O0.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED.ReactCurrentOwner, W0 = { key: !0, ref: !0, __self: !0, __source: !0 };
function Nd(e, t, n) {
  var r, o = {}, i = null, s = null;
  n !== void 0 && (i = "" + n), t.key !== void 0 && (i = "" + t.key), t.ref !== void 0 && (s = t.ref);
  for (r in t) V0.call(t, r) && !W0.hasOwnProperty(r) && (o[r] = t[r]);
  if (e && e.defaultProps) for (r in t = e.defaultProps, t) o[r] === void 0 && (o[r] = t[r]);
  return { $$typeof: F0, type: e, key: i, ref: s, props: o, _owner: B0.current };
}
ds.Fragment = H0;
ds.jsx = Nd;
ds.jsxs = Nd;
gd.exports = ds;
var S = gd.exports, Cd = { exports: {} }, Ke = {}, Md = { exports: {} }, Pd = {};
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
      var F = L - 1 >>> 1, R = N[F];
      if (0 < o(R, C)) N[F] = C, N[L] = R, L = F;
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
      e: for (var F = 0, R = N.length, U = R >>> 1; F < U; ) {
        var W = 2 * (F + 1) - 1, X = N[W], b = W + 1, K = N[b];
        if (0 > o(X, L)) b < R && 0 > o(K, X) ? (N[F] = K, N[b] = L, F = b) : (N[F] = X, N[W] = L, F = W);
        else if (b < R && 0 > o(K, L)) N[F] = K, N[b] = L, F = b;
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
    if (w = !1, h(N), !y) if (n(u) !== null) y = !0, I(_);
    else {
      var C = n(a);
      C !== null && $(v, C.startTime - N);
    }
  }
  function _(N, C) {
    y = !1, w && (w = !1, p(P), P = -1), m = !0;
    var L = f;
    try {
      for (h(C), c = n(u); c !== null && (!(c.expirationTime > C) || N && !z()); ) {
        var F = c.callback;
        if (typeof F == "function") {
          c.callback = null, f = c.priorityLevel;
          var R = F(c.expirationTime <= C);
          C = e.unstable_now(), typeof R == "function" ? c.callback = R : c === n(u) && r(u), h(C);
        } else r(u);
        c = n(u);
      }
      if (c !== null) var U = !0;
      else {
        var W = n(a);
        W !== null && $(v, W.startTime - C), U = !1;
      }
      return U;
    } finally {
      c = null, f = L, m = !1;
    }
  }
  var E = !1, M = null, P = -1, T = 5, O = -1;
  function z() {
    return !(e.unstable_now() - O < T);
  }
  function D() {
    if (M !== null) {
      var N = e.unstable_now();
      O = N;
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
  function I(N) {
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
    y || m || (y = !0, I(_));
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
    var F = e.unstable_now();
    switch (typeof L == "object" && L !== null ? (L = L.delay, L = typeof L == "number" && 0 < L ? F + L : F) : L = F, N) {
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
    return R = L + R, N = { id: d++, callback: C, priorityLevel: N, startTime: L, expirationTime: R, sortIndex: -1 }, L > F ? (N.sortIndex = L, t(a, N), n(u) === null && N === n(a) && (w ? (p(P), P = -1) : w = !0, $(v, L - F))) : (N.sortIndex = R, t(u, N), y || m || (y = !0, I(_))), N;
  }, e.unstable_shouldYield = z, e.unstable_wrapCallback = function(N) {
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
})(Pd);
Md.exports = Pd;
var U0 = Md.exports;
/**
 * @license React
 * react-dom.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var Y0 = j, be = U0;
function V(e) {
  for (var t = "https://reactjs.org/docs/error-decoder.html?invariant=" + e, n = 1; n < arguments.length; n++) t += "&args[]=" + encodeURIComponent(arguments[n]);
  return "Minified React error #" + e + "; visit " + t + " for the full message or use the non-minified dev environment for full errors and additional helpful warnings.";
}
var Id = /* @__PURE__ */ new Set(), to = {};
function Ln(e, t) {
  ir(e, t), ir(e + "Capture", t);
}
function ir(e, t) {
  for (to[e] = t, e = 0; e < t.length; e++) Id.add(t[e]);
}
var Lt = !(typeof window > "u" || typeof window.document > "u" || typeof window.document.createElement > "u"), Ml = Object.prototype.hasOwnProperty, X0 = /^[:A-Z_a-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF\u0370-\u037D\u037F-\u1FFF\u200C-\u200D\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD][:A-Z_a-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF\u0370-\u037D\u037F-\u1FFF\u200C-\u200D\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD\-.0-9\u00B7\u0300-\u036F\u203F-\u2040]*$/, oc = {}, ic = {};
function b0(e) {
  return Ml.call(ic, e) ? !0 : Ml.call(oc, e) ? !1 : X0.test(e) ? ic[e] = !0 : (oc[e] = !0, !1);
}
function Q0(e, t, n, r) {
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
function K0(e, t, n, r) {
  if (t === null || typeof t > "u" || Q0(e, t, n, r)) return !0;
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
var Fu = /[\-:]([a-z])/g;
function Hu(e) {
  return e[1].toUpperCase();
}
"accent-height alignment-baseline arabic-form baseline-shift cap-height clip-path clip-rule color-interpolation color-interpolation-filters color-profile color-rendering dominant-baseline enable-background fill-opacity fill-rule flood-color flood-opacity font-family font-size font-size-adjust font-stretch font-style font-variant font-weight glyph-name glyph-orientation-horizontal glyph-orientation-vertical horiz-adv-x horiz-origin-x image-rendering letter-spacing lighting-color marker-end marker-mid marker-start overline-position overline-thickness paint-order panose-1 pointer-events rendering-intent shape-rendering stop-color stop-opacity strikethrough-position strikethrough-thickness stroke-dasharray stroke-dashoffset stroke-linecap stroke-linejoin stroke-miterlimit stroke-opacity stroke-width text-anchor text-decoration text-rendering underline-position underline-thickness unicode-bidi unicode-range units-per-em v-alphabetic v-hanging v-ideographic v-mathematical vector-effect vert-adv-y vert-origin-x vert-origin-y word-spacing writing-mode xmlns:xlink x-height".split(" ").forEach(function(e) {
  var t = e.replace(
    Fu,
    Hu
  );
  Me[t] = new Re(t, 1, !1, e, null, !1, !1);
});
"xlink:actuate xlink:arcrole xlink:role xlink:show xlink:title xlink:type".split(" ").forEach(function(e) {
  var t = e.replace(Fu, Hu);
  Me[t] = new Re(t, 1, !1, e, "http://www.w3.org/1999/xlink", !1, !1);
});
["xml:base", "xml:lang", "xml:space"].forEach(function(e) {
  var t = e.replace(Fu, Hu);
  Me[t] = new Re(t, 1, !1, e, "http://www.w3.org/XML/1998/namespace", !1, !1);
});
["tabIndex", "crossOrigin"].forEach(function(e) {
  Me[e] = new Re(e, 1, !1, e.toLowerCase(), null, !1, !1);
});
Me.xlinkHref = new Re("xlinkHref", 1, !1, "xlink:href", "http://www.w3.org/1999/xlink", !0, !1);
["src", "href", "action", "formAction"].forEach(function(e) {
  Me[e] = new Re(e, 1, !1, e.toLowerCase(), null, !0, !0);
});
function Vu(e, t, n, r) {
  var o = Me.hasOwnProperty(t) ? Me[t] : null;
  (o !== null ? o.type !== 0 : r || !(2 < t.length) || t[0] !== "o" && t[0] !== "O" || t[1] !== "n" && t[1] !== "N") && (K0(t, n, o, r) && (n = null), r || o === null ? b0(t) && (n === null ? e.removeAttribute(t) : e.setAttribute(t, "" + n)) : o.mustUseProperty ? e[o.propertyName] = n === null ? o.type === 3 ? !1 : "" : n : (t = o.attributeName, r = o.attributeNamespace, n === null ? e.removeAttribute(t) : (o = o.type, n = o === 3 || o === 4 && n === !0 ? "" : "" + n, r ? e.setAttributeNS(r, t, n) : e.setAttribute(t, n))));
}
var jt = Y0.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED, Wo = Symbol.for("react.element"), jn = Symbol.for("react.portal"), On = Symbol.for("react.fragment"), Bu = Symbol.for("react.strict_mode"), Pl = Symbol.for("react.profiler"), zd = Symbol.for("react.provider"), Td = Symbol.for("react.context"), Wu = Symbol.for("react.forward_ref"), Il = Symbol.for("react.suspense"), zl = Symbol.for("react.suspense_list"), Uu = Symbol.for("react.memo"), Wt = Symbol.for("react.lazy"), Ld = Symbol.for("react.offscreen"), sc = Symbol.iterator;
function Pr(e) {
  return e === null || typeof e != "object" ? null : (e = sc && e[sc] || e["@@iterator"], typeof e == "function" ? e : null);
}
var me = Object.assign, Xs;
function Or(e) {
  if (Xs === void 0) try {
    throw Error();
  } catch (n) {
    var t = n.stack.trim().match(/\n( *(at )?)/);
    Xs = t && t[1] || "";
  }
  return `
` + Xs + e;
}
var bs = !1;
function Qs(e, t) {
  if (!e || bs) return "";
  bs = !0;
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
    bs = !1, Error.prepareStackTrace = n;
  }
  return (e = e ? e.displayName || e.name : "") ? Or(e) : "";
}
function G0(e) {
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
function Tl(e) {
  if (e == null) return null;
  if (typeof e == "function") return e.displayName || e.name || null;
  if (typeof e == "string") return e;
  switch (e) {
    case On:
      return "Fragment";
    case jn:
      return "Portal";
    case Pl:
      return "Profiler";
    case Bu:
      return "StrictMode";
    case Il:
      return "Suspense";
    case zl:
      return "SuspenseList";
  }
  if (typeof e == "object") switch (e.$$typeof) {
    case Td:
      return (e.displayName || "Context") + ".Consumer";
    case zd:
      return (e._context.displayName || "Context") + ".Provider";
    case Wu:
      var t = e.render;
      return e = e.displayName, e || (e = t.displayName || t.name || "", e = e !== "" ? "ForwardRef(" + e + ")" : "ForwardRef"), e;
    case Uu:
      return t = e.displayName || null, t !== null ? t : Tl(e.type) || "Memo";
    case Wt:
      t = e._payload, e = e._init;
      try {
        return Tl(e(t));
      } catch {
      }
  }
  return null;
}
function Z0(e) {
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
      return Tl(t);
    case 8:
      return t === Bu ? "StrictMode" : "Mode";
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
function Ad(e) {
  var t = e.type;
  return (e = e.nodeName) && e.toLowerCase() === "input" && (t === "checkbox" || t === "radio");
}
function q0(e) {
  var t = Ad(e) ? "checked" : "value", n = Object.getOwnPropertyDescriptor(e.constructor.prototype, t), r = "" + e[t];
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
function Uo(e) {
  e._valueTracker || (e._valueTracker = q0(e));
}
function $d(e) {
  if (!e) return !1;
  var t = e._valueTracker;
  if (!t) return !0;
  var n = t.getValue(), r = "";
  return e && (r = Ad(e) ? e.checked ? "true" : "false" : e.value), e = r, e !== n ? (t.setValue(e), !0) : !1;
}
function Ti(e) {
  if (e = e || (typeof document < "u" ? document : void 0), typeof e > "u") return null;
  try {
    return e.activeElement || e.body;
  } catch {
    return e.body;
  }
}
function Ll(e, t) {
  var n = t.checked;
  return me({}, t, { defaultChecked: void 0, defaultValue: void 0, value: void 0, checked: n ?? e._wrapperState.initialChecked });
}
function lc(e, t) {
  var n = t.defaultValue == null ? "" : t.defaultValue, r = t.checked != null ? t.checked : t.defaultChecked;
  n = sn(t.value != null ? t.value : n), e._wrapperState = { initialChecked: r, initialValue: n, controlled: t.type === "checkbox" || t.type === "radio" ? t.checked != null : t.value != null };
}
function Dd(e, t) {
  t = t.checked, t != null && Vu(e, "checked", t, !1);
}
function Al(e, t) {
  Dd(e, t);
  var n = sn(t.value), r = t.type;
  if (n != null) r === "number" ? (n === 0 && e.value === "" || e.value != n) && (e.value = "" + n) : e.value !== "" + n && (e.value = "" + n);
  else if (r === "submit" || r === "reset") {
    e.removeAttribute("value");
    return;
  }
  t.hasOwnProperty("value") ? $l(e, t.type, n) : t.hasOwnProperty("defaultValue") && $l(e, t.type, sn(t.defaultValue)), t.checked == null && t.defaultChecked != null && (e.defaultChecked = !!t.defaultChecked);
}
function uc(e, t, n) {
  if (t.hasOwnProperty("value") || t.hasOwnProperty("defaultValue")) {
    var r = t.type;
    if (!(r !== "submit" && r !== "reset" || t.value !== void 0 && t.value !== null)) return;
    t = "" + e._wrapperState.initialValue, n || t === e.value || (e.value = t), e.defaultValue = t;
  }
  n = e.name, n !== "" && (e.name = ""), e.defaultChecked = !!e._wrapperState.initialChecked, n !== "" && (e.name = n);
}
function $l(e, t, n) {
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
function Dl(e, t) {
  if (t.dangerouslySetInnerHTML != null) throw Error(V(91));
  return me({}, t, { value: void 0, defaultValue: void 0, children: "" + e._wrapperState.initialValue });
}
function ac(e, t) {
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
function Rd(e, t) {
  var n = sn(t.value), r = sn(t.defaultValue);
  n != null && (n = "" + n, n !== e.value && (e.value = n), t.defaultValue == null && e.defaultValue !== n && (e.defaultValue = n)), r != null && (e.defaultValue = "" + r);
}
function cc(e) {
  var t = e.textContent;
  t === e._wrapperState.initialValue && t !== "" && t !== null && (e.value = t);
}
function jd(e) {
  switch (e) {
    case "svg":
      return "http://www.w3.org/2000/svg";
    case "math":
      return "http://www.w3.org/1998/Math/MathML";
    default:
      return "http://www.w3.org/1999/xhtml";
  }
}
function Rl(e, t) {
  return e == null || e === "http://www.w3.org/1999/xhtml" ? jd(t) : e === "http://www.w3.org/2000/svg" && t === "foreignObject" ? "http://www.w3.org/1999/xhtml" : e;
}
var Yo, Od = function(e) {
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
var Ur = {
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
}, J0 = ["Webkit", "ms", "Moz", "O"];
Object.keys(Ur).forEach(function(e) {
  J0.forEach(function(t) {
    t = t + e.charAt(0).toUpperCase() + e.substring(1), Ur[t] = Ur[e];
  });
});
function Fd(e, t, n) {
  return t == null || typeof t == "boolean" || t === "" ? "" : n || typeof t != "number" || t === 0 || Ur.hasOwnProperty(e) && Ur[e] ? ("" + t).trim() : t + "px";
}
function Hd(e, t) {
  e = e.style;
  for (var n in t) if (t.hasOwnProperty(n)) {
    var r = n.indexOf("--") === 0, o = Fd(n, t[n], r);
    n === "float" && (n = "cssFloat"), r ? e.setProperty(n, o) : e[n] = o;
  }
}
var ey = me({ menuitem: !0 }, { area: !0, base: !0, br: !0, col: !0, embed: !0, hr: !0, img: !0, input: !0, keygen: !0, link: !0, meta: !0, param: !0, source: !0, track: !0, wbr: !0 });
function jl(e, t) {
  if (t) {
    if (ey[e] && (t.children != null || t.dangerouslySetInnerHTML != null)) throw Error(V(137, e));
    if (t.dangerouslySetInnerHTML != null) {
      if (t.children != null) throw Error(V(60));
      if (typeof t.dangerouslySetInnerHTML != "object" || !("__html" in t.dangerouslySetInnerHTML)) throw Error(V(61));
    }
    if (t.style != null && typeof t.style != "object") throw Error(V(62));
  }
}
function Ol(e, t) {
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
var Fl = null;
function Yu(e) {
  return e = e.target || e.srcElement || window, e.correspondingUseElement && (e = e.correspondingUseElement), e.nodeType === 3 ? e.parentNode : e;
}
var Hl = null, qn = null, Jn = null;
function fc(e) {
  if (e = Lo(e)) {
    if (typeof Hl != "function") throw Error(V(280));
    var t = e.stateNode;
    t && (t = ys(t), Hl(e.stateNode, e.type, t));
  }
}
function Vd(e) {
  qn ? Jn ? Jn.push(e) : Jn = [e] : qn = e;
}
function Bd() {
  if (qn) {
    var e = qn, t = Jn;
    if (Jn = qn = null, fc(e), t) for (e = 0; e < t.length; e++) fc(t[e]);
  }
}
function Wd(e, t) {
  return e(t);
}
function Ud() {
}
var Ks = !1;
function Yd(e, t, n) {
  if (Ks) return e(t, n);
  Ks = !0;
  try {
    return Wd(e, t, n);
  } finally {
    Ks = !1, (qn !== null || Jn !== null) && (Ud(), Bd());
  }
}
function ro(e, t) {
  var n = e.stateNode;
  if (n === null) return null;
  var r = ys(n);
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
var Vl = !1;
if (Lt) try {
  var Ir = {};
  Object.defineProperty(Ir, "passive", { get: function() {
    Vl = !0;
  } }), window.addEventListener("test", Ir, Ir), window.removeEventListener("test", Ir, Ir);
} catch {
  Vl = !1;
}
function ty(e, t, n, r, o, i, s, l, u) {
  var a = Array.prototype.slice.call(arguments, 3);
  try {
    t.apply(n, a);
  } catch (d) {
    this.onError(d);
  }
}
var Yr = !1, Li = null, Ai = !1, Bl = null, ny = { onError: function(e) {
  Yr = !0, Li = e;
} };
function ry(e, t, n, r, o, i, s, l, u) {
  Yr = !1, Li = null, ty.apply(ny, arguments);
}
function oy(e, t, n, r, o, i, s, l, u) {
  if (ry.apply(this, arguments), Yr) {
    if (Yr) {
      var a = Li;
      Yr = !1, Li = null;
    } else throw Error(V(198));
    Ai || (Ai = !0, Bl = a);
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
function Xd(e) {
  if (e.tag === 13) {
    var t = e.memoizedState;
    if (t === null && (e = e.alternate, e !== null && (t = e.memoizedState)), t !== null) return t.dehydrated;
  }
  return null;
}
function dc(e) {
  if (An(e) !== e) throw Error(V(188));
}
function iy(e) {
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
        if (i === n) return dc(o), e;
        if (i === r) return dc(o), t;
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
function bd(e) {
  return e = iy(e), e !== null ? Qd(e) : null;
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
var Kd = be.unstable_scheduleCallback, hc = be.unstable_cancelCallback, sy = be.unstable_shouldYield, ly = be.unstable_requestPaint, ve = be.unstable_now, uy = be.unstable_getCurrentPriorityLevel, Xu = be.unstable_ImmediatePriority, Gd = be.unstable_UserBlockingPriority, $i = be.unstable_NormalPriority, ay = be.unstable_LowPriority, Zd = be.unstable_IdlePriority, hs = null, wt = null;
function cy(e) {
  if (wt && typeof wt.onCommitFiberRoot == "function") try {
    wt.onCommitFiberRoot(hs, e, void 0, (e.current.flags & 128) === 128);
  } catch {
  }
}
var ft = Math.clz32 ? Math.clz32 : hy, fy = Math.log, dy = Math.LN2;
function hy(e) {
  return e >>>= 0, e === 0 ? 32 : 31 - (fy(e) / dy | 0) | 0;
}
var Xo = 64, bo = 4194304;
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
function py(e, t) {
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
function gy(e, t) {
  for (var n = e.suspendedLanes, r = e.pingedLanes, o = e.expirationTimes, i = e.pendingLanes; 0 < i; ) {
    var s = 31 - ft(i), l = 1 << s, u = o[s];
    u === -1 ? (!(l & n) || l & r) && (o[s] = py(l, t)) : u <= t && (e.expiredLanes |= l), i &= ~l;
  }
}
function Wl(e) {
  return e = e.pendingLanes & -1073741825, e !== 0 ? e : e & 1073741824 ? 1073741824 : 0;
}
function qd() {
  var e = Xo;
  return Xo <<= 1, !(Xo & 4194240) && (Xo = 64), e;
}
function Gs(e) {
  for (var t = [], n = 0; 31 > n; n++) t.push(e);
  return t;
}
function zo(e, t, n) {
  e.pendingLanes |= t, t !== 536870912 && (e.suspendedLanes = 0, e.pingedLanes = 0), e = e.eventTimes, t = 31 - ft(t), e[t] = n;
}
function my(e, t) {
  var n = e.pendingLanes & ~t;
  e.pendingLanes = t, e.suspendedLanes = 0, e.pingedLanes = 0, e.expiredLanes &= t, e.mutableReadLanes &= t, e.entangledLanes &= t, t = e.entanglements;
  var r = e.eventTimes;
  for (e = e.expirationTimes; 0 < n; ) {
    var o = 31 - ft(n), i = 1 << o;
    t[o] = 0, r[o] = -1, e[o] = -1, n &= ~i;
  }
}
function bu(e, t) {
  var n = e.entangledLanes |= t;
  for (e = e.entanglements; n; ) {
    var r = 31 - ft(n), o = 1 << r;
    o & t | e[r] & t && (e[r] |= t), n &= ~o;
  }
}
var se = 0;
function Jd(e) {
  return e &= -e, 1 < e ? 4 < e ? e & 268435455 ? 16 : 536870912 : 4 : 1;
}
var eh, Qu, th, nh, rh, Ul = !1, Qo = [], Gt = null, Zt = null, qt = null, oo = /* @__PURE__ */ new Map(), io = /* @__PURE__ */ new Map(), Yt = [], yy = "mousedown mouseup touchcancel touchend touchstart auxclick dblclick pointercancel pointerdown pointerup dragend dragstart drop compositionend compositionstart keydown keypress keyup input textInput copy cut paste click change contextmenu reset submit".split(" ");
function pc(e, t) {
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
function zr(e, t, n, r, o, i) {
  return e === null || e.nativeEvent !== i ? (e = { blockedOn: t, domEventName: n, eventSystemFlags: r, nativeEvent: i, targetContainers: [o] }, t !== null && (t = Lo(t), t !== null && Qu(t)), e) : (e.eventSystemFlags |= r, t = e.targetContainers, o !== null && t.indexOf(o) === -1 && t.push(o), e);
}
function vy(e, t, n, r, o) {
  switch (t) {
    case "focusin":
      return Gt = zr(Gt, e, t, n, r, o), !0;
    case "dragenter":
      return Zt = zr(Zt, e, t, n, r, o), !0;
    case "mouseover":
      return qt = zr(qt, e, t, n, r, o), !0;
    case "pointerover":
      var i = o.pointerId;
      return oo.set(i, zr(oo.get(i) || null, e, t, n, r, o)), !0;
    case "gotpointercapture":
      return i = o.pointerId, io.set(i, zr(io.get(i) || null, e, t, n, r, o)), !0;
  }
  return !1;
}
function oh(e) {
  var t = gn(e.target);
  if (t !== null) {
    var n = An(t);
    if (n !== null) {
      if (t = n.tag, t === 13) {
        if (t = Xd(n), t !== null) {
          e.blockedOn = t, rh(e.priority, function() {
            th(n);
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
    var n = Yl(e.domEventName, e.eventSystemFlags, t[0], e.nativeEvent);
    if (n === null) {
      n = e.nativeEvent;
      var r = new n.constructor(n.type, n);
      Fl = r, n.target.dispatchEvent(r), Fl = null;
    } else return t = Lo(n), t !== null && Qu(t), e.blockedOn = n, !1;
    t.shift();
  }
  return !0;
}
function gc(e, t, n) {
  mi(e) && n.delete(t);
}
function wy() {
  Ul = !1, Gt !== null && mi(Gt) && (Gt = null), Zt !== null && mi(Zt) && (Zt = null), qt !== null && mi(qt) && (qt = null), oo.forEach(gc), io.forEach(gc);
}
function Tr(e, t) {
  e.blockedOn === t && (e.blockedOn = null, Ul || (Ul = !0, be.unstable_scheduleCallback(be.unstable_NormalPriority, wy)));
}
function so(e) {
  function t(o) {
    return Tr(o, e);
  }
  if (0 < Qo.length) {
    Tr(Qo[0], e);
    for (var n = 1; n < Qo.length; n++) {
      var r = Qo[n];
      r.blockedOn === e && (r.blockedOn = null);
    }
  }
  for (Gt !== null && Tr(Gt, e), Zt !== null && Tr(Zt, e), qt !== null && Tr(qt, e), oo.forEach(t), io.forEach(t), n = 0; n < Yt.length; n++) r = Yt[n], r.blockedOn === e && (r.blockedOn = null);
  for (; 0 < Yt.length && (n = Yt[0], n.blockedOn === null); ) oh(n), n.blockedOn === null && Yt.shift();
}
var er = jt.ReactCurrentBatchConfig, Ri = !0;
function xy(e, t, n, r) {
  var o = se, i = er.transition;
  er.transition = null;
  try {
    se = 1, Ku(e, t, n, r);
  } finally {
    se = o, er.transition = i;
  }
}
function Sy(e, t, n, r) {
  var o = se, i = er.transition;
  er.transition = null;
  try {
    se = 4, Ku(e, t, n, r);
  } finally {
    se = o, er.transition = i;
  }
}
function Ku(e, t, n, r) {
  if (Ri) {
    var o = Yl(e, t, n, r);
    if (o === null) sl(e, t, r, ji, n), pc(e, r);
    else if (vy(o, e, t, n, r)) r.stopPropagation();
    else if (pc(e, r), t & 4 && -1 < yy.indexOf(e)) {
      for (; o !== null; ) {
        var i = Lo(o);
        if (i !== null && eh(i), i = Yl(e, t, n, r), i === null && sl(e, t, r, ji, n), i === o) break;
        o = i;
      }
      o !== null && r.stopPropagation();
    } else sl(e, t, r, null, n);
  }
}
var ji = null;
function Yl(e, t, n, r) {
  if (ji = null, e = Yu(r), e = gn(e), e !== null) if (t = An(e), t === null) e = null;
  else if (n = t.tag, n === 13) {
    if (e = Xd(t), e !== null) return e;
    e = null;
  } else if (n === 3) {
    if (t.stateNode.current.memoizedState.isDehydrated) return t.tag === 3 ? t.stateNode.containerInfo : null;
    e = null;
  } else t !== e && (e = null);
  return ji = e, null;
}
function ih(e) {
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
      switch (uy()) {
        case Xu:
          return 1;
        case Gd:
          return 4;
        case $i:
        case ay:
          return 16;
        case Zd:
          return 536870912;
        default:
          return 16;
      }
    default:
      return 16;
  }
}
var Qt = null, Gu = null, yi = null;
function sh() {
  if (yi) return yi;
  var e, t = Gu, n = t.length, r, o = "value" in Qt ? Qt.value : Qt.textContent, i = o.length;
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
function mc() {
  return !1;
}
function Ge(e) {
  function t(n, r, o, i, s) {
    this._reactName = n, this._targetInst = o, this.type = r, this.nativeEvent = i, this.target = s, this.currentTarget = null;
    for (var l in e) e.hasOwnProperty(l) && (n = e[l], this[l] = n ? n(i) : i[l]);
    return this.isDefaultPrevented = (i.defaultPrevented != null ? i.defaultPrevented : i.returnValue === !1) ? Ko : mc, this.isPropagationStopped = mc, this;
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
}, defaultPrevented: 0, isTrusted: 0 }, Zu = Ge(_r), To = me({}, _r, { view: 0, detail: 0 }), _y = Ge(To), Zs, qs, Lr, ps = me({}, To, { screenX: 0, screenY: 0, clientX: 0, clientY: 0, pageX: 0, pageY: 0, ctrlKey: 0, shiftKey: 0, altKey: 0, metaKey: 0, getModifierState: qu, button: 0, buttons: 0, relatedTarget: function(e) {
  return e.relatedTarget === void 0 ? e.fromElement === e.srcElement ? e.toElement : e.fromElement : e.relatedTarget;
}, movementX: function(e) {
  return "movementX" in e ? e.movementX : (e !== Lr && (Lr && e.type === "mousemove" ? (Zs = e.screenX - Lr.screenX, qs = e.screenY - Lr.screenY) : qs = Zs = 0, Lr = e), Zs);
}, movementY: function(e) {
  return "movementY" in e ? e.movementY : qs;
} }), yc = Ge(ps), Ey = me({}, ps, { dataTransfer: 0 }), ky = Ge(Ey), Ny = me({}, To, { relatedTarget: 0 }), Js = Ge(Ny), Cy = me({}, _r, { animationName: 0, elapsedTime: 0, pseudoElement: 0 }), My = Ge(Cy), Py = me({}, _r, { clipboardData: function(e) {
  return "clipboardData" in e ? e.clipboardData : window.clipboardData;
} }), Iy = Ge(Py), zy = me({}, _r, { data: 0 }), vc = Ge(zy), Ty = {
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
}, Ly = {
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
}, Ay = { Alt: "altKey", Control: "ctrlKey", Meta: "metaKey", Shift: "shiftKey" };
function $y(e) {
  var t = this.nativeEvent;
  return t.getModifierState ? t.getModifierState(e) : (e = Ay[e]) ? !!t[e] : !1;
}
function qu() {
  return $y;
}
var Dy = me({}, To, { key: function(e) {
  if (e.key) {
    var t = Ty[e.key] || e.key;
    if (t !== "Unidentified") return t;
  }
  return e.type === "keypress" ? (e = vi(e), e === 13 ? "Enter" : String.fromCharCode(e)) : e.type === "keydown" || e.type === "keyup" ? Ly[e.keyCode] || "Unidentified" : "";
}, code: 0, location: 0, ctrlKey: 0, shiftKey: 0, altKey: 0, metaKey: 0, repeat: 0, locale: 0, getModifierState: qu, charCode: function(e) {
  return e.type === "keypress" ? vi(e) : 0;
}, keyCode: function(e) {
  return e.type === "keydown" || e.type === "keyup" ? e.keyCode : 0;
}, which: function(e) {
  return e.type === "keypress" ? vi(e) : e.type === "keydown" || e.type === "keyup" ? e.keyCode : 0;
} }), Ry = Ge(Dy), jy = me({}, ps, { pointerId: 0, width: 0, height: 0, pressure: 0, tangentialPressure: 0, tiltX: 0, tiltY: 0, twist: 0, pointerType: 0, isPrimary: 0 }), wc = Ge(jy), Oy = me({}, To, { touches: 0, targetTouches: 0, changedTouches: 0, altKey: 0, metaKey: 0, ctrlKey: 0, shiftKey: 0, getModifierState: qu }), Fy = Ge(Oy), Hy = me({}, _r, { propertyName: 0, elapsedTime: 0, pseudoElement: 0 }), Vy = Ge(Hy), By = me({}, ps, {
  deltaX: function(e) {
    return "deltaX" in e ? e.deltaX : "wheelDeltaX" in e ? -e.wheelDeltaX : 0;
  },
  deltaY: function(e) {
    return "deltaY" in e ? e.deltaY : "wheelDeltaY" in e ? -e.wheelDeltaY : "wheelDelta" in e ? -e.wheelDelta : 0;
  },
  deltaZ: 0,
  deltaMode: 0
}), Wy = Ge(By), Uy = [9, 13, 27, 32], Ju = Lt && "CompositionEvent" in window, Xr = null;
Lt && "documentMode" in document && (Xr = document.documentMode);
var Yy = Lt && "TextEvent" in window && !Xr, lh = Lt && (!Ju || Xr && 8 < Xr && 11 >= Xr), xc = " ", Sc = !1;
function uh(e, t) {
  switch (e) {
    case "keyup":
      return Uy.indexOf(t.keyCode) !== -1;
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
function ah(e) {
  return e = e.detail, typeof e == "object" && "data" in e ? e.data : null;
}
var Fn = !1;
function Xy(e, t) {
  switch (e) {
    case "compositionend":
      return ah(t);
    case "keypress":
      return t.which !== 32 ? null : (Sc = !0, xc);
    case "textInput":
      return e = t.data, e === xc && Sc ? null : e;
    default:
      return null;
  }
}
function by(e, t) {
  if (Fn) return e === "compositionend" || !Ju && uh(e, t) ? (e = sh(), yi = Gu = Qt = null, Fn = !1, e) : null;
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
      return lh && t.locale !== "ko" ? null : t.data;
    default:
      return null;
  }
}
var Qy = { color: !0, date: !0, datetime: !0, "datetime-local": !0, email: !0, month: !0, number: !0, password: !0, range: !0, search: !0, tel: !0, text: !0, time: !0, url: !0, week: !0 };
function _c(e) {
  var t = e && e.nodeName && e.nodeName.toLowerCase();
  return t === "input" ? !!Qy[e.type] : t === "textarea";
}
function ch(e, t, n, r) {
  Vd(r), t = Oi(t, "onChange"), 0 < t.length && (n = new Zu("onChange", "change", null, n, r), e.push({ event: n, listeners: t }));
}
var br = null, lo = null;
function Ky(e) {
  Sh(e, 0);
}
function gs(e) {
  var t = Bn(e);
  if ($d(t)) return e;
}
function Gy(e, t) {
  if (e === "change") return t;
}
var fh = !1;
if (Lt) {
  var el;
  if (Lt) {
    var tl = "oninput" in document;
    if (!tl) {
      var Ec = document.createElement("div");
      Ec.setAttribute("oninput", "return;"), tl = typeof Ec.oninput == "function";
    }
    el = tl;
  } else el = !1;
  fh = el && (!document.documentMode || 9 < document.documentMode);
}
function kc() {
  br && (br.detachEvent("onpropertychange", dh), lo = br = null);
}
function dh(e) {
  if (e.propertyName === "value" && gs(lo)) {
    var t = [];
    ch(t, lo, e, Yu(e)), Yd(Ky, t);
  }
}
function Zy(e, t, n) {
  e === "focusin" ? (kc(), br = t, lo = n, br.attachEvent("onpropertychange", dh)) : e === "focusout" && kc();
}
function qy(e) {
  if (e === "selectionchange" || e === "keyup" || e === "keydown") return gs(lo);
}
function Jy(e, t) {
  if (e === "click") return gs(t);
}
function ev(e, t) {
  if (e === "input" || e === "change") return gs(t);
}
function tv(e, t) {
  return e === t && (e !== 0 || 1 / e === 1 / t) || e !== e && t !== t;
}
var ht = typeof Object.is == "function" ? Object.is : tv;
function uo(e, t) {
  if (ht(e, t)) return !0;
  if (typeof e != "object" || e === null || typeof t != "object" || t === null) return !1;
  var n = Object.keys(e), r = Object.keys(t);
  if (n.length !== r.length) return !1;
  for (r = 0; r < n.length; r++) {
    var o = n[r];
    if (!Ml.call(t, o) || !ht(e[o], t[o])) return !1;
  }
  return !0;
}
function Nc(e) {
  for (; e && e.firstChild; ) e = e.firstChild;
  return e;
}
function Cc(e, t) {
  var n = Nc(e);
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
    n = Nc(n);
  }
}
function hh(e, t) {
  return e && t ? e === t ? !0 : e && e.nodeType === 3 ? !1 : t && t.nodeType === 3 ? hh(e, t.parentNode) : "contains" in e ? e.contains(t) : e.compareDocumentPosition ? !!(e.compareDocumentPosition(t) & 16) : !1 : !1;
}
function ph() {
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
function ea(e) {
  var t = e && e.nodeName && e.nodeName.toLowerCase();
  return t && (t === "input" && (e.type === "text" || e.type === "search" || e.type === "tel" || e.type === "url" || e.type === "password") || t === "textarea" || e.contentEditable === "true");
}
function nv(e) {
  var t = ph(), n = e.focusedElem, r = e.selectionRange;
  if (t !== n && n && n.ownerDocument && hh(n.ownerDocument.documentElement, n)) {
    if (r !== null && ea(n)) {
      if (t = r.start, e = r.end, e === void 0 && (e = t), "selectionStart" in n) n.selectionStart = t, n.selectionEnd = Math.min(e, n.value.length);
      else if (e = (t = n.ownerDocument || document) && t.defaultView || window, e.getSelection) {
        e = e.getSelection();
        var o = n.textContent.length, i = Math.min(r.start, o);
        r = r.end === void 0 ? i : Math.min(r.end, o), !e.extend && i > r && (o = r, r = i, i = o), o = Cc(n, i);
        var s = Cc(
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
var rv = Lt && "documentMode" in document && 11 >= document.documentMode, Hn = null, Xl = null, Qr = null, bl = !1;
function Mc(e, t, n) {
  var r = n.window === n ? n.document : n.nodeType === 9 ? n : n.ownerDocument;
  bl || Hn == null || Hn !== Ti(r) || (r = Hn, "selectionStart" in r && ea(r) ? r = { start: r.selectionStart, end: r.selectionEnd } : (r = (r.ownerDocument && r.ownerDocument.defaultView || window).getSelection(), r = { anchorNode: r.anchorNode, anchorOffset: r.anchorOffset, focusNode: r.focusNode, focusOffset: r.focusOffset }), Qr && uo(Qr, r) || (Qr = r, r = Oi(Xl, "onSelect"), 0 < r.length && (t = new Zu("onSelect", "select", null, t, n), e.push({ event: t, listeners: r }), t.target = Hn)));
}
function Go(e, t) {
  var n = {};
  return n[e.toLowerCase()] = t.toLowerCase(), n["Webkit" + e] = "webkit" + t, n["Moz" + e] = "moz" + t, n;
}
var Vn = { animationend: Go("Animation", "AnimationEnd"), animationiteration: Go("Animation", "AnimationIteration"), animationstart: Go("Animation", "AnimationStart"), transitionend: Go("Transition", "TransitionEnd") }, nl = {}, gh = {};
Lt && (gh = document.createElement("div").style, "AnimationEvent" in window || (delete Vn.animationend.animation, delete Vn.animationiteration.animation, delete Vn.animationstart.animation), "TransitionEvent" in window || delete Vn.transitionend.transition);
function ms(e) {
  if (nl[e]) return nl[e];
  if (!Vn[e]) return e;
  var t = Vn[e], n;
  for (n in t) if (t.hasOwnProperty(n) && n in gh) return nl[e] = t[n];
  return e;
}
var mh = ms("animationend"), yh = ms("animationiteration"), vh = ms("animationstart"), wh = ms("transitionend"), xh = /* @__PURE__ */ new Map(), Pc = "abort auxClick cancel canPlay canPlayThrough click close contextMenu copy cut drag dragEnd dragEnter dragExit dragLeave dragOver dragStart drop durationChange emptied encrypted ended error gotPointerCapture input invalid keyDown keyPress keyUp load loadedData loadedMetadata loadStart lostPointerCapture mouseDown mouseMove mouseOut mouseOver mouseUp paste pause play playing pointerCancel pointerDown pointerMove pointerOut pointerOver pointerUp progress rateChange reset resize seeked seeking stalled submit suspend timeUpdate touchCancel touchEnd touchStart volumeChange scroll toggle touchMove waiting wheel".split(" ");
function un(e, t) {
  xh.set(e, t), Ln(t, [e]);
}
for (var rl = 0; rl < Pc.length; rl++) {
  var ol = Pc[rl], ov = ol.toLowerCase(), iv = ol[0].toUpperCase() + ol.slice(1);
  un(ov, "on" + iv);
}
un(mh, "onAnimationEnd");
un(yh, "onAnimationIteration");
un(vh, "onAnimationStart");
un("dblclick", "onDoubleClick");
un("focusin", "onFocus");
un("focusout", "onBlur");
un(wh, "onTransitionEnd");
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
var Vr = "abort canplay canplaythrough durationchange emptied encrypted ended error loadeddata loadedmetadata loadstart pause play playing progress ratechange resize seeked seeking stalled suspend timeupdate volumechange waiting".split(" "), sv = new Set("cancel close invalid load scroll toggle".split(" ").concat(Vr));
function Ic(e, t, n) {
  var r = e.type || "unknown-event";
  e.currentTarget = n, oy(r, t, void 0, e), e.currentTarget = null;
}
function Sh(e, t) {
  t = (t & 4) !== 0;
  for (var n = 0; n < e.length; n++) {
    var r = e[n], o = r.event;
    r = r.listeners;
    e: {
      var i = void 0;
      if (t) for (var s = r.length - 1; 0 <= s; s--) {
        var l = r[s], u = l.instance, a = l.currentTarget;
        if (l = l.listener, u !== i && o.isPropagationStopped()) break e;
        Ic(o, l, a), i = u;
      }
      else for (s = 0; s < r.length; s++) {
        if (l = r[s], u = l.instance, a = l.currentTarget, l = l.listener, u !== i && o.isPropagationStopped()) break e;
        Ic(o, l, a), i = u;
      }
    }
  }
  if (Ai) throw e = Bl, Ai = !1, Bl = null, e;
}
function ae(e, t) {
  var n = t[ql];
  n === void 0 && (n = t[ql] = /* @__PURE__ */ new Set());
  var r = e + "__bubble";
  n.has(r) || (_h(t, e, 2, !1), n.add(r));
}
function il(e, t, n) {
  var r = 0;
  t && (r |= 4), _h(n, e, r, t);
}
var Zo = "_reactListening" + Math.random().toString(36).slice(2);
function ao(e) {
  if (!e[Zo]) {
    e[Zo] = !0, Id.forEach(function(n) {
      n !== "selectionchange" && (sv.has(n) || il(n, !1, e), il(n, !0, e));
    });
    var t = e.nodeType === 9 ? e : e.ownerDocument;
    t === null || t[Zo] || (t[Zo] = !0, il("selectionchange", !1, t));
  }
}
function _h(e, t, n, r) {
  switch (ih(t)) {
    case 1:
      var o = xy;
      break;
    case 4:
      o = Sy;
      break;
    default:
      o = Ku;
  }
  n = o.bind(null, t, n, e), o = void 0, !Vl || t !== "touchstart" && t !== "touchmove" && t !== "wheel" || (o = !0), r ? o !== void 0 ? e.addEventListener(t, n, { capture: !0, passive: o }) : e.addEventListener(t, n, !0) : o !== void 0 ? e.addEventListener(t, n, { passive: o }) : e.addEventListener(t, n, !1);
}
function sl(e, t, n, r, o) {
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
  Yd(function() {
    var a = i, d = Yu(n), c = [];
    e: {
      var f = xh.get(e);
      if (f !== void 0) {
        var m = Zu, y = e;
        switch (e) {
          case "keypress":
            if (vi(n) === 0) break e;
          case "keydown":
          case "keyup":
            m = Ry;
            break;
          case "focusin":
            y = "focus", m = Js;
            break;
          case "focusout":
            y = "blur", m = Js;
            break;
          case "beforeblur":
          case "afterblur":
            m = Js;
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
            m = yc;
            break;
          case "drag":
          case "dragend":
          case "dragenter":
          case "dragexit":
          case "dragleave":
          case "dragover":
          case "dragstart":
          case "drop":
            m = ky;
            break;
          case "touchcancel":
          case "touchend":
          case "touchmove":
          case "touchstart":
            m = Fy;
            break;
          case mh:
          case yh:
          case vh:
            m = My;
            break;
          case wh:
            m = Vy;
            break;
          case "scroll":
            m = _y;
            break;
          case "wheel":
            m = Wy;
            break;
          case "copy":
          case "cut":
          case "paste":
            m = Iy;
            break;
          case "gotpointercapture":
          case "lostpointercapture":
          case "pointercancel":
          case "pointerdown":
          case "pointermove":
          case "pointerout":
          case "pointerover":
          case "pointerup":
            m = wc;
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
        if (f = e === "mouseover" || e === "pointerover", m = e === "mouseout" || e === "pointerout", f && n !== Fl && (y = n.relatedTarget || n.fromElement) && (gn(y) || y[At])) break e;
        if ((m || f) && (f = d.window === d ? d : (f = d.ownerDocument) ? f.defaultView || f.parentWindow : window, m ? (y = n.relatedTarget || n.toElement, m = a, y = y ? gn(y) : null, y !== null && (x = An(y), y !== x || y.tag !== 5 && y.tag !== 6) && (y = null)) : (m = null, y = a), m !== y)) {
          if (w = yc, v = "onMouseLeave", p = "onMouseEnter", g = "mouse", (e === "pointerout" || e === "pointerover") && (w = wc, v = "onPointerLeave", p = "onPointerEnter", g = "pointer"), x = m == null ? f : Bn(m), h = y == null ? f : Bn(y), f = new w(v, g + "leave", m, n, d), f.target = x, f.relatedTarget = h, v = null, gn(d) === a && (w = new w(p, g + "enter", y, n, d), w.target = h, w.relatedTarget = x, v = w), x = v, m && y) t: {
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
        if (f = a ? Bn(a) : window, m = f.nodeName && f.nodeName.toLowerCase(), m === "select" || m === "input" && f.type === "file") var _ = Gy;
        else if (_c(f)) if (fh) _ = ev;
        else {
          _ = qy;
          var E = Zy;
        }
        else (m = f.nodeName) && m.toLowerCase() === "input" && (f.type === "checkbox" || f.type === "radio") && (_ = Jy);
        if (_ && (_ = _(e, a))) {
          ch(c, _, n, d);
          break e;
        }
        E && E(e, f, a), e === "focusout" && (E = f._wrapperState) && E.controlled && f.type === "number" && $l(f, "number", f.value);
      }
      switch (E = a ? Bn(a) : window, e) {
        case "focusin":
          (_c(E) || E.contentEditable === "true") && (Hn = E, Xl = a, Qr = null);
          break;
        case "focusout":
          Qr = Xl = Hn = null;
          break;
        case "mousedown":
          bl = !0;
          break;
        case "contextmenu":
        case "mouseup":
        case "dragend":
          bl = !1, Mc(c, n, d);
          break;
        case "selectionchange":
          if (rv) break;
        case "keydown":
        case "keyup":
          Mc(c, n, d);
      }
      var M;
      if (Ju) e: {
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
      else Fn ? uh(e, n) && (P = "onCompositionEnd") : e === "keydown" && n.keyCode === 229 && (P = "onCompositionStart");
      P && (lh && n.locale !== "ko" && (Fn || P !== "onCompositionStart" ? P === "onCompositionEnd" && Fn && (M = sh()) : (Qt = d, Gu = "value" in Qt ? Qt.value : Qt.textContent, Fn = !0)), E = Oi(a, P), 0 < E.length && (P = new vc(P, e, null, n, d), c.push({ event: P, listeners: E }), M ? P.data = M : (M = ah(n), M !== null && (P.data = M)))), (M = Yy ? Xy(e, n) : by(e, n)) && (a = Oi(a, "onBeforeInput"), 0 < a.length && (d = new vc("onBeforeInput", "beforeinput", null, n, d), c.push({ event: d, listeners: a }), d.data = M));
    }
    Sh(c, t);
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
var lv = /\r\n?/g, uv = /\u0000|\uFFFD/g;
function Tc(e) {
  return (typeof e == "string" ? e : "" + e).replace(lv, `
`).replace(uv, "");
}
function qo(e, t, n) {
  if (t = Tc(t), Tc(e) !== t && n) throw Error(V(425));
}
function Fi() {
}
var Ql = null, Kl = null;
function Gl(e, t) {
  return e === "textarea" || e === "noscript" || typeof t.children == "string" || typeof t.children == "number" || typeof t.dangerouslySetInnerHTML == "object" && t.dangerouslySetInnerHTML !== null && t.dangerouslySetInnerHTML.__html != null;
}
var Zl = typeof setTimeout == "function" ? setTimeout : void 0, av = typeof clearTimeout == "function" ? clearTimeout : void 0, Lc = typeof Promise == "function" ? Promise : void 0, cv = typeof queueMicrotask == "function" ? queueMicrotask : typeof Lc < "u" ? function(e) {
  return Lc.resolve(null).then(e).catch(fv);
} : Zl;
function fv(e) {
  setTimeout(function() {
    throw e;
  });
}
function ll(e, t) {
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
function Ac(e) {
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
var Er = Math.random().toString(36).slice(2), vt = "__reactFiber$" + Er, fo = "__reactProps$" + Er, At = "__reactContainer$" + Er, ql = "__reactEvents$" + Er, dv = "__reactListeners$" + Er, hv = "__reactHandles$" + Er;
function gn(e) {
  var t = e[vt];
  if (t) return t;
  for (var n = e.parentNode; n; ) {
    if (t = n[At] || n[vt]) {
      if (n = t.alternate, t.child !== null || n !== null && n.child !== null) for (e = Ac(e); e !== null; ) {
        if (n = e[vt]) return n;
        e = Ac(e);
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
function ys(e) {
  return e[fo] || null;
}
var Jl = [], Wn = -1;
function an(e) {
  return { current: e };
}
function ce(e) {
  0 > Wn || (e.current = Jl[Wn], Jl[Wn] = null, Wn--);
}
function le(e, t) {
  Wn++, Jl[Wn] = e.current, e.current = t;
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
function $c(e, t, n) {
  if (Le.current !== ln) throw Error(V(168));
  le(Le, t), le(He, n);
}
function Eh(e, t, n) {
  var r = e.stateNode;
  if (t = t.childContextTypes, typeof r.getChildContext != "function") return n;
  r = r.getChildContext();
  for (var o in r) if (!(o in t)) throw Error(V(108, Z0(e) || "Unknown", o));
  return me({}, n, r);
}
function Vi(e) {
  return e = (e = e.stateNode) && e.__reactInternalMemoizedMergedChildContext || ln, En = Le.current, le(Le, e), le(He, He.current), !0;
}
function Dc(e, t, n) {
  var r = e.stateNode;
  if (!r) throw Error(V(169));
  n ? (e = Eh(e, t, En), r.__reactInternalMemoizedMergedChildContext = e, ce(He), ce(Le), le(Le, e)) : ce(He), le(He, n);
}
var Mt = null, vs = !1, ul = !1;
function kh(e) {
  Mt === null ? Mt = [e] : Mt.push(e);
}
function pv(e) {
  vs = !0, kh(e);
}
function cn() {
  if (!ul && Mt !== null) {
    ul = !0;
    var e = 0, t = se;
    try {
      var n = Mt;
      for (se = 1; e < n.length; e++) {
        var r = n[e];
        do
          r = r(!0);
        while (r !== null);
      }
      Mt = null, vs = !1;
    } catch (o) {
      throw Mt !== null && (Mt = Mt.slice(e + 1)), Kd(Xu, cn), o;
    } finally {
      se = t, ul = !1;
    }
  }
  return null;
}
var Un = [], Yn = 0, Bi = null, Wi = 0, Ze = [], qe = 0, kn = null, Pt = 1, It = "";
function dn(e, t) {
  Un[Yn++] = Wi, Un[Yn++] = Bi, Bi = e, Wi = t;
}
function Nh(e, t, n) {
  Ze[qe++] = Pt, Ze[qe++] = It, Ze[qe++] = kn, kn = e;
  var r = Pt;
  e = It;
  var o = 32 - ft(r) - 1;
  r &= ~(1 << o), n += 1;
  var i = 32 - ft(t) + o;
  if (30 < i) {
    var s = o - o % 5;
    i = (r & (1 << s) - 1).toString(32), r >>= s, o -= s, Pt = 1 << 32 - ft(t) + o | n << o | r, It = i + e;
  } else Pt = 1 << i | n << o | r, It = e;
}
function ta(e) {
  e.return !== null && (dn(e, 1), Nh(e, 1, 0));
}
function na(e) {
  for (; e === Bi; ) Bi = Un[--Yn], Un[Yn] = null, Wi = Un[--Yn], Un[Yn] = null;
  for (; e === kn; ) kn = Ze[--qe], Ze[qe] = null, It = Ze[--qe], Ze[qe] = null, Pt = Ze[--qe], Ze[qe] = null;
}
var Xe = null, Ye = null, fe = !1, lt = null;
function Ch(e, t) {
  var n = Je(5, null, null, 0);
  n.elementType = "DELETED", n.stateNode = t, n.return = e, t = e.deletions, t === null ? (e.deletions = [n], e.flags |= 16) : t.push(n);
}
function Rc(e, t) {
  switch (e.tag) {
    case 5:
      var n = e.type;
      return t = t.nodeType !== 1 || n.toLowerCase() !== t.nodeName.toLowerCase() ? null : t, t !== null ? (e.stateNode = t, Xe = e, Ye = Jt(t.firstChild), !0) : !1;
    case 6:
      return t = e.pendingProps === "" || t.nodeType !== 3 ? null : t, t !== null ? (e.stateNode = t, Xe = e, Ye = null, !0) : !1;
    case 13:
      return t = t.nodeType !== 8 ? null : t, t !== null ? (n = kn !== null ? { id: Pt, overflow: It } : null, e.memoizedState = { dehydrated: t, treeContext: n, retryLane: 1073741824 }, n = Je(18, null, null, 0), n.stateNode = t, n.return = e, e.child = n, Xe = e, Ye = null, !0) : !1;
    default:
      return !1;
  }
}
function eu(e) {
  return (e.mode & 1) !== 0 && (e.flags & 128) === 0;
}
function tu(e) {
  if (fe) {
    var t = Ye;
    if (t) {
      var n = t;
      if (!Rc(e, t)) {
        if (eu(e)) throw Error(V(418));
        t = Jt(n.nextSibling);
        var r = Xe;
        t && Rc(e, t) ? Ch(r, n) : (e.flags = e.flags & -4097 | 2, fe = !1, Xe = e);
      }
    } else {
      if (eu(e)) throw Error(V(418));
      e.flags = e.flags & -4097 | 2, fe = !1, Xe = e;
    }
  }
}
function jc(e) {
  for (e = e.return; e !== null && e.tag !== 5 && e.tag !== 3 && e.tag !== 13; ) e = e.return;
  Xe = e;
}
function Jo(e) {
  if (e !== Xe) return !1;
  if (!fe) return jc(e), fe = !0, !1;
  var t;
  if ((t = e.tag !== 3) && !(t = e.tag !== 5) && (t = e.type, t = t !== "head" && t !== "body" && !Gl(e.type, e.memoizedProps)), t && (t = Ye)) {
    if (eu(e)) throw Mh(), Error(V(418));
    for (; t; ) Ch(e, t), t = Jt(t.nextSibling);
  }
  if (jc(e), e.tag === 13) {
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
function Mh() {
  for (var e = Ye; e; ) e = Jt(e.nextSibling);
}
function lr() {
  Ye = Xe = null, fe = !1;
}
function ra(e) {
  lt === null ? lt = [e] : lt.push(e);
}
var gv = jt.ReactCurrentBatchConfig;
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
function Oc(e) {
  var t = e._init;
  return t(e._payload);
}
function Ph(e) {
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
    return g === null || g.tag !== 6 ? (g = gl(h, p.mode, v), g.return = p, g) : (g = o(g, h), g.return = p, g);
  }
  function u(p, g, h, v) {
    var _ = h.type;
    return _ === On ? d(p, g, h.props.children, v, h.key) : g !== null && (g.elementType === _ || typeof _ == "object" && _ !== null && _.$$typeof === Wt && Oc(_) === g.type) ? (v = o(g, h.props), v.ref = Ar(p, g, h), v.return = p, v) : (v = Ni(h.type, h.key, h.props, null, p.mode, v), v.ref = Ar(p, g, h), v.return = p, v);
  }
  function a(p, g, h, v) {
    return g === null || g.tag !== 4 || g.stateNode.containerInfo !== h.containerInfo || g.stateNode.implementation !== h.implementation ? (g = ml(h, p.mode, v), g.return = p, g) : (g = o(g, h.children || []), g.return = p, g);
  }
  function d(p, g, h, v, _) {
    return g === null || g.tag !== 7 ? (g = xn(h, p.mode, v, _), g.return = p, g) : (g = o(g, h), g.return = p, g);
  }
  function c(p, g, h) {
    if (typeof g == "string" && g !== "" || typeof g == "number") return g = gl("" + g, p.mode, h), g.return = p, g;
    if (typeof g == "object" && g !== null) {
      switch (g.$$typeof) {
        case Wo:
          return h = Ni(g.type, g.key, g.props, null, p.mode, h), h.ref = Ar(p, null, g), h.return = p, h;
        case jn:
          return g = ml(g, p.mode, h), g.return = p, g;
        case Wt:
          var v = g._init;
          return c(p, v(g._payload), h);
      }
      if (Fr(g) || Pr(g)) return g = xn(g, p.mode, h, null), g.return = p, g;
      ei(p, g);
    }
    return null;
  }
  function f(p, g, h, v) {
    var _ = g !== null ? g.key : null;
    if (typeof h == "string" && h !== "" || typeof h == "number") return _ !== null ? null : l(p, g, "" + h, v);
    if (typeof h == "object" && h !== null) {
      switch (h.$$typeof) {
        case Wo:
          return h.key === _ ? u(p, g, h, v) : null;
        case jn:
          return h.key === _ ? a(p, g, h, v) : null;
        case Wt:
          return _ = h._init, f(
            p,
            g,
            _(h._payload),
            v
          );
      }
      if (Fr(h) || Pr(h)) return _ !== null ? null : d(p, g, h, v, null);
      ei(p, h);
    }
    return null;
  }
  function m(p, g, h, v, _) {
    if (typeof v == "string" && v !== "" || typeof v == "number") return p = p.get(h) || null, l(g, p, "" + v, _);
    if (typeof v == "object" && v !== null) {
      switch (v.$$typeof) {
        case Wo:
          return p = p.get(v.key === null ? h : v.key) || null, u(g, p, v, _);
        case jn:
          return p = p.get(v.key === null ? h : v.key) || null, a(g, p, v, _);
        case Wt:
          var E = v._init;
          return m(p, g, h, E(v._payload), _);
      }
      if (Fr(v) || Pr(v)) return p = p.get(h) || null, d(g, p, v, _, null);
      ei(g, v);
    }
    return null;
  }
  function y(p, g, h, v) {
    for (var _ = null, E = null, M = g, P = g = 0, T = null; M !== null && P < h.length; P++) {
      M.index > P ? (T = M, M = null) : T = M.sibling;
      var O = f(p, M, h[P], v);
      if (O === null) {
        M === null && (M = T);
        break;
      }
      e && M && O.alternate === null && t(p, M), g = i(O, g, P), E === null ? _ = O : E.sibling = O, E = O, M = T;
    }
    if (P === h.length) return n(p, M), fe && dn(p, P), _;
    if (M === null) {
      for (; P < h.length; P++) M = c(p, h[P], v), M !== null && (g = i(M, g, P), E === null ? _ = M : E.sibling = M, E = M);
      return fe && dn(p, P), _;
    }
    for (M = r(p, M); P < h.length; P++) T = m(M, p, P, h[P], v), T !== null && (e && T.alternate !== null && M.delete(T.key === null ? P : T.key), g = i(T, g, P), E === null ? _ = T : E.sibling = T, E = T);
    return e && M.forEach(function(z) {
      return t(p, z);
    }), fe && dn(p, P), _;
  }
  function w(p, g, h, v) {
    var _ = Pr(h);
    if (typeof _ != "function") throw Error(V(150));
    if (h = _.call(h), h == null) throw Error(V(151));
    for (var E = _ = null, M = g, P = g = 0, T = null, O = h.next(); M !== null && !O.done; P++, O = h.next()) {
      M.index > P ? (T = M, M = null) : T = M.sibling;
      var z = f(p, M, O.value, v);
      if (z === null) {
        M === null && (M = T);
        break;
      }
      e && M && z.alternate === null && t(p, M), g = i(z, g, P), E === null ? _ = z : E.sibling = z, E = z, M = T;
    }
    if (O.done) return n(
      p,
      M
    ), fe && dn(p, P), _;
    if (M === null) {
      for (; !O.done; P++, O = h.next()) O = c(p, O.value, v), O !== null && (g = i(O, g, P), E === null ? _ = O : E.sibling = O, E = O);
      return fe && dn(p, P), _;
    }
    for (M = r(p, M); !O.done; P++, O = h.next()) O = m(M, p, P, O.value, v), O !== null && (e && O.alternate !== null && M.delete(O.key === null ? P : O.key), g = i(O, g, P), E === null ? _ = O : E.sibling = O, E = O);
    return e && M.forEach(function(D) {
      return t(p, D);
    }), fe && dn(p, P), _;
  }
  function x(p, g, h, v) {
    if (typeof h == "object" && h !== null && h.type === On && h.key === null && (h = h.props.children), typeof h == "object" && h !== null) {
      switch (h.$$typeof) {
        case Wo:
          e: {
            for (var _ = h.key, E = g; E !== null; ) {
              if (E.key === _) {
                if (_ = h.type, _ === On) {
                  if (E.tag === 7) {
                    n(p, E.sibling), g = o(E, h.props.children), g.return = p, p = g;
                    break e;
                  }
                } else if (E.elementType === _ || typeof _ == "object" && _ !== null && _.$$typeof === Wt && Oc(_) === E.type) {
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
            g = ml(h, p.mode, v), g.return = p, p = g;
          }
          return s(p);
        case Wt:
          return E = h._init, x(p, g, E(h._payload), v);
      }
      if (Fr(h)) return y(p, g, h, v);
      if (Pr(h)) return w(p, g, h, v);
      ei(p, h);
    }
    return typeof h == "string" && h !== "" || typeof h == "number" ? (h = "" + h, g !== null && g.tag === 6 ? (n(p, g.sibling), g = o(g, h), g.return = p, p = g) : (n(p, g), g = gl(h, p.mode, v), g.return = p, p = g), s(p)) : n(p, g);
  }
  return x;
}
var ur = Ph(!0), Ih = Ph(!1), Ui = an(null), Yi = null, Xn = null, oa = null;
function ia() {
  oa = Xn = Yi = null;
}
function sa(e) {
  var t = Ui.current;
  ce(Ui), e._currentValue = t;
}
function nu(e, t, n) {
  for (; e !== null; ) {
    var r = e.alternate;
    if ((e.childLanes & t) !== t ? (e.childLanes |= t, r !== null && (r.childLanes |= t)) : r !== null && (r.childLanes & t) !== t && (r.childLanes |= t), e === n) break;
    e = e.return;
  }
}
function tr(e, t) {
  Yi = e, oa = Xn = null, e = e.dependencies, e !== null && e.firstContext !== null && (e.lanes & t && (Oe = !0), e.firstContext = null);
}
function tt(e) {
  var t = e._currentValue;
  if (oa !== e) if (e = { context: e, memoizedValue: t, next: null }, Xn === null) {
    if (Yi === null) throw Error(V(308));
    Xn = e, Yi.dependencies = { lanes: 0, firstContext: e };
  } else Xn = Xn.next = e;
  return t;
}
var mn = null;
function la(e) {
  mn === null ? mn = [e] : mn.push(e);
}
function zh(e, t, n, r) {
  var o = t.interleaved;
  return o === null ? (n.next = n, la(t)) : (n.next = o.next, o.next = n), t.interleaved = n, $t(e, r);
}
function $t(e, t) {
  e.lanes |= t;
  var n = e.alternate;
  for (n !== null && (n.lanes |= t), n = e, e = e.return; e !== null; ) e.childLanes |= t, n = e.alternate, n !== null && (n.childLanes |= t), n = e, e = e.return;
  return n.tag === 3 ? n.stateNode : null;
}
var Ut = !1;
function ua(e) {
  e.updateQueue = { baseState: e.memoizedState, firstBaseUpdate: null, lastBaseUpdate: null, shared: { pending: null, interleaved: null, lanes: 0 }, effects: null };
}
function Th(e, t) {
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
  return o = r.interleaved, o === null ? (t.next = t, la(r)) : (t.next = o.next, o.next = t), r.interleaved = t, $t(e, n);
}
function wi(e, t, n) {
  if (t = t.updateQueue, t !== null && (t = t.shared, (n & 4194240) !== 0)) {
    var r = t.lanes;
    r &= e.pendingLanes, n |= r, t.lanes = n, bu(e, n);
  }
}
function Fc(e, t) {
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
  Ut = !1;
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
              Ut = !0;
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
function Hc(e, t, n) {
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
function aa(e, t) {
  switch (le(po, t), le(ho, e), le(xt, Ao), e = t.nodeType, e) {
    case 9:
    case 11:
      t = (t = t.documentElement) ? t.namespaceURI : Rl(null, "");
      break;
    default:
      e = e === 8 ? t.parentNode : t, t = e.namespaceURI || null, e = e.tagName, t = Rl(t, e);
  }
  ce(xt), le(xt, t);
}
function ar() {
  ce(xt), ce(ho), ce(po);
}
function Lh(e) {
  yn(po.current);
  var t = yn(xt.current), n = Rl(t, e.type);
  t !== n && (le(ho, e), le(xt, n));
}
function ca(e) {
  ho.current === e && (ce(xt), ce(ho));
}
var pe = an(0);
function bi(e) {
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
var al = [];
function fa() {
  for (var e = 0; e < al.length; e++) al[e]._workInProgressVersionPrimary = null;
  al.length = 0;
}
var xi = jt.ReactCurrentDispatcher, cl = jt.ReactCurrentBatchConfig, Nn = 0, ge = null, Se = null, Ee = null, Qi = !1, Kr = !1, go = 0, mv = 0;
function Ie() {
  throw Error(V(321));
}
function da(e, t) {
  if (t === null) return !1;
  for (var n = 0; n < t.length && n < e.length; n++) if (!ht(e[n], t[n])) return !1;
  return !0;
}
function ha(e, t, n, r, o, i) {
  if (Nn = i, ge = t, t.memoizedState = null, t.updateQueue = null, t.lanes = 0, xi.current = e === null || e.memoizedState === null ? xv : Sv, e = n(r, o), Kr) {
    i = 0;
    do {
      if (Kr = !1, go = 0, 25 <= i) throw Error(V(301));
      i += 1, Ee = Se = null, t.updateQueue = null, xi.current = _v, e = n(r, o);
    } while (Kr);
  }
  if (xi.current = Ki, t = Se !== null && Se.next !== null, Nn = 0, Ee = Se = ge = null, Qi = !1, t) throw Error(V(300));
  return e;
}
function pa() {
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
function fl(e) {
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
function dl(e) {
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
function Ah() {
}
function $h(e, t) {
  var n = ge, r = nt(), o = t(), i = !ht(r.memoizedState, o);
  if (i && (r.memoizedState = o, Oe = !0), r = r.queue, ga(jh.bind(null, n, r, e), [e]), r.getSnapshot !== t || i || Ee !== null && Ee.memoizedState.tag & 1) {
    if (n.flags |= 2048, yo(9, Rh.bind(null, n, r, o, t), void 0, null), ke === null) throw Error(V(349));
    Nn & 30 || Dh(n, t, o);
  }
  return o;
}
function Dh(e, t, n) {
  e.flags |= 16384, e = { getSnapshot: t, value: n }, t = ge.updateQueue, t === null ? (t = { lastEffect: null, stores: null }, ge.updateQueue = t, t.stores = [e]) : (n = t.stores, n === null ? t.stores = [e] : n.push(e));
}
function Rh(e, t, n, r) {
  t.value = n, t.getSnapshot = r, Oh(t) && Fh(e);
}
function jh(e, t, n) {
  return n(function() {
    Oh(t) && Fh(e);
  });
}
function Oh(e) {
  var t = e.getSnapshot;
  e = e.value;
  try {
    var n = t();
    return !ht(e, n);
  } catch {
    return !0;
  }
}
function Fh(e) {
  var t = $t(e, 1);
  t !== null && dt(t, e, 1, -1);
}
function Vc(e) {
  var t = mt();
  return typeof e == "function" && (e = e()), t.memoizedState = t.baseState = e, e = { pending: null, interleaved: null, lanes: 0, dispatch: null, lastRenderedReducer: mo, lastRenderedState: e }, t.queue = e, e = e.dispatch = wv.bind(null, ge, e), [t.memoizedState, e];
}
function yo(e, t, n, r) {
  return e = { tag: e, create: t, destroy: n, deps: r, next: null }, t = ge.updateQueue, t === null ? (t = { lastEffect: null, stores: null }, ge.updateQueue = t, t.lastEffect = e.next = e) : (n = t.lastEffect, n === null ? t.lastEffect = e.next = e : (r = n.next, n.next = e, e.next = r, t.lastEffect = e)), e;
}
function Hh() {
  return nt().memoizedState;
}
function Si(e, t, n, r) {
  var o = mt();
  ge.flags |= e, o.memoizedState = yo(1 | t, n, void 0, r === void 0 ? null : r);
}
function ws(e, t, n, r) {
  var o = nt();
  r = r === void 0 ? null : r;
  var i = void 0;
  if (Se !== null) {
    var s = Se.memoizedState;
    if (i = s.destroy, r !== null && da(r, s.deps)) {
      o.memoizedState = yo(t, n, i, r);
      return;
    }
  }
  ge.flags |= e, o.memoizedState = yo(1 | t, n, i, r);
}
function Bc(e, t) {
  return Si(8390656, 8, e, t);
}
function ga(e, t) {
  return ws(2048, 8, e, t);
}
function Vh(e, t) {
  return ws(4, 2, e, t);
}
function Bh(e, t) {
  return ws(4, 4, e, t);
}
function Wh(e, t) {
  if (typeof t == "function") return e = e(), t(e), function() {
    t(null);
  };
  if (t != null) return e = e(), t.current = e, function() {
    t.current = null;
  };
}
function Uh(e, t, n) {
  return n = n != null ? n.concat([e]) : null, ws(4, 4, Wh.bind(null, t, e), n);
}
function ma() {
}
function Yh(e, t) {
  var n = nt();
  t = t === void 0 ? null : t;
  var r = n.memoizedState;
  return r !== null && t !== null && da(t, r[1]) ? r[0] : (n.memoizedState = [e, t], e);
}
function Xh(e, t) {
  var n = nt();
  t = t === void 0 ? null : t;
  var r = n.memoizedState;
  return r !== null && t !== null && da(t, r[1]) ? r[0] : (e = e(), n.memoizedState = [e, t], e);
}
function bh(e, t, n) {
  return Nn & 21 ? (ht(n, t) || (n = qd(), ge.lanes |= n, Cn |= n, e.baseState = !0), t) : (e.baseState && (e.baseState = !1, Oe = !0), e.memoizedState = n);
}
function yv(e, t) {
  var n = se;
  se = n !== 0 && 4 > n ? n : 4, e(!0);
  var r = cl.transition;
  cl.transition = {};
  try {
    e(!1), t();
  } finally {
    se = n, cl.transition = r;
  }
}
function Qh() {
  return nt().memoizedState;
}
function vv(e, t, n) {
  var r = nn(e);
  if (n = { lane: r, action: n, hasEagerState: !1, eagerState: null, next: null }, Kh(e)) Gh(t, n);
  else if (n = zh(e, t, n, r), n !== null) {
    var o = $e();
    dt(n, e, r, o), Zh(n, t, r);
  }
}
function wv(e, t, n) {
  var r = nn(e), o = { lane: r, action: n, hasEagerState: !1, eagerState: null, next: null };
  if (Kh(e)) Gh(t, o);
  else {
    var i = e.alternate;
    if (e.lanes === 0 && (i === null || i.lanes === 0) && (i = t.lastRenderedReducer, i !== null)) try {
      var s = t.lastRenderedState, l = i(s, n);
      if (o.hasEagerState = !0, o.eagerState = l, ht(l, s)) {
        var u = t.interleaved;
        u === null ? (o.next = o, la(t)) : (o.next = u.next, u.next = o), t.interleaved = o;
        return;
      }
    } catch {
    } finally {
    }
    n = zh(e, t, o, r), n !== null && (o = $e(), dt(n, e, r, o), Zh(n, t, r));
  }
}
function Kh(e) {
  var t = e.alternate;
  return e === ge || t !== null && t === ge;
}
function Gh(e, t) {
  Kr = Qi = !0;
  var n = e.pending;
  n === null ? t.next = t : (t.next = n.next, n.next = t), e.pending = t;
}
function Zh(e, t, n) {
  if (n & 4194240) {
    var r = t.lanes;
    r &= e.pendingLanes, n |= r, t.lanes = n, bu(e, n);
  }
}
var Ki = { readContext: tt, useCallback: Ie, useContext: Ie, useEffect: Ie, useImperativeHandle: Ie, useInsertionEffect: Ie, useLayoutEffect: Ie, useMemo: Ie, useReducer: Ie, useRef: Ie, useState: Ie, useDebugValue: Ie, useDeferredValue: Ie, useTransition: Ie, useMutableSource: Ie, useSyncExternalStore: Ie, useId: Ie, unstable_isNewReconciler: !1 }, xv = { readContext: tt, useCallback: function(e, t) {
  return mt().memoizedState = [e, t === void 0 ? null : t], e;
}, useContext: tt, useEffect: Bc, useImperativeHandle: function(e, t, n) {
  return n = n != null ? n.concat([e]) : null, Si(
    4194308,
    4,
    Wh.bind(null, t, e),
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
  return t = n !== void 0 ? n(t) : t, r.memoizedState = r.baseState = t, e = { pending: null, interleaved: null, lanes: 0, dispatch: null, lastRenderedReducer: e, lastRenderedState: t }, r.queue = e, e = e.dispatch = vv.bind(null, ge, e), [r.memoizedState, e];
}, useRef: function(e) {
  var t = mt();
  return e = { current: e }, t.memoizedState = e;
}, useState: Vc, useDebugValue: ma, useDeferredValue: function(e) {
  return mt().memoizedState = e;
}, useTransition: function() {
  var e = Vc(!1), t = e[0];
  return e = yv.bind(null, e[1]), mt().memoizedState = e, [t, e];
}, useMutableSource: function() {
}, useSyncExternalStore: function(e, t, n) {
  var r = ge, o = mt();
  if (fe) {
    if (n === void 0) throw Error(V(407));
    n = n();
  } else {
    if (n = t(), ke === null) throw Error(V(349));
    Nn & 30 || Dh(r, t, n);
  }
  o.memoizedState = n;
  var i = { value: n, getSnapshot: t };
  return o.queue = i, Bc(jh.bind(
    null,
    r,
    i,
    e
  ), [e]), r.flags |= 2048, yo(9, Rh.bind(null, r, i, n, t), void 0, null), n;
}, useId: function() {
  var e = mt(), t = ke.identifierPrefix;
  if (fe) {
    var n = It, r = Pt;
    n = (r & ~(1 << 32 - ft(r) - 1)).toString(32) + n, t = ":" + t + "R" + n, n = go++, 0 < n && (t += "H" + n.toString(32)), t += ":";
  } else n = mv++, t = ":" + t + "r" + n.toString(32) + ":";
  return e.memoizedState = t;
}, unstable_isNewReconciler: !1 }, Sv = {
  readContext: tt,
  useCallback: Yh,
  useContext: tt,
  useEffect: ga,
  useImperativeHandle: Uh,
  useInsertionEffect: Vh,
  useLayoutEffect: Bh,
  useMemo: Xh,
  useReducer: fl,
  useRef: Hh,
  useState: function() {
    return fl(mo);
  },
  useDebugValue: ma,
  useDeferredValue: function(e) {
    var t = nt();
    return bh(t, Se.memoizedState, e);
  },
  useTransition: function() {
    var e = fl(mo)[0], t = nt().memoizedState;
    return [e, t];
  },
  useMutableSource: Ah,
  useSyncExternalStore: $h,
  useId: Qh,
  unstable_isNewReconciler: !1
}, _v = { readContext: tt, useCallback: Yh, useContext: tt, useEffect: ga, useImperativeHandle: Uh, useInsertionEffect: Vh, useLayoutEffect: Bh, useMemo: Xh, useReducer: dl, useRef: Hh, useState: function() {
  return dl(mo);
}, useDebugValue: ma, useDeferredValue: function(e) {
  var t = nt();
  return Se === null ? t.memoizedState = e : bh(t, Se.memoizedState, e);
}, useTransition: function() {
  var e = dl(mo)[0], t = nt().memoizedState;
  return [e, t];
}, useMutableSource: Ah, useSyncExternalStore: $h, useId: Qh, unstable_isNewReconciler: !1 };
function ot(e, t) {
  if (e && e.defaultProps) {
    t = me({}, t), e = e.defaultProps;
    for (var n in e) t[n] === void 0 && (t[n] = e[n]);
    return t;
  }
  return t;
}
function ru(e, t, n, r) {
  t = e.memoizedState, n = n(r, t), n = n == null ? t : me({}, t, n), e.memoizedState = n, e.lanes === 0 && (e.updateQueue.baseState = n);
}
var xs = { isMounted: function(e) {
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
function Wc(e, t, n, r, o, i, s) {
  return e = e.stateNode, typeof e.shouldComponentUpdate == "function" ? e.shouldComponentUpdate(r, i, s) : t.prototype && t.prototype.isPureReactComponent ? !uo(n, r) || !uo(o, i) : !0;
}
function qh(e, t, n) {
  var r = !1, o = ln, i = t.contextType;
  return typeof i == "object" && i !== null ? i = tt(i) : (o = Ve(t) ? En : Le.current, r = t.contextTypes, i = (r = r != null) ? sr(e, o) : ln), t = new t(n, i), e.memoizedState = t.state !== null && t.state !== void 0 ? t.state : null, t.updater = xs, e.stateNode = t, t._reactInternals = e, r && (e = e.stateNode, e.__reactInternalMemoizedUnmaskedChildContext = o, e.__reactInternalMemoizedMaskedChildContext = i), t;
}
function Uc(e, t, n, r) {
  e = t.state, typeof t.componentWillReceiveProps == "function" && t.componentWillReceiveProps(n, r), typeof t.UNSAFE_componentWillReceiveProps == "function" && t.UNSAFE_componentWillReceiveProps(n, r), t.state !== e && xs.enqueueReplaceState(t, t.state, null);
}
function ou(e, t, n, r) {
  var o = e.stateNode;
  o.props = n, o.state = e.memoizedState, o.refs = {}, ua(e);
  var i = t.contextType;
  typeof i == "object" && i !== null ? o.context = tt(i) : (i = Ve(t) ? En : Le.current, o.context = sr(e, i)), o.state = e.memoizedState, i = t.getDerivedStateFromProps, typeof i == "function" && (ru(e, t, i, n), o.state = e.memoizedState), typeof t.getDerivedStateFromProps == "function" || typeof o.getSnapshotBeforeUpdate == "function" || typeof o.UNSAFE_componentWillMount != "function" && typeof o.componentWillMount != "function" || (t = o.state, typeof o.componentWillMount == "function" && o.componentWillMount(), typeof o.UNSAFE_componentWillMount == "function" && o.UNSAFE_componentWillMount(), t !== o.state && xs.enqueueReplaceState(o, o.state, null), Xi(e, n, o, r), o.state = e.memoizedState), typeof o.componentDidMount == "function" && (e.flags |= 4194308);
}
function cr(e, t) {
  try {
    var n = "", r = t;
    do
      n += G0(r), r = r.return;
    while (r);
    var o = n;
  } catch (i) {
    o = `
Error generating stack: ` + i.message + `
` + i.stack;
  }
  return { value: e, source: t, stack: o, digest: null };
}
function hl(e, t, n) {
  return { value: e, source: null, stack: n ?? null, digest: t ?? null };
}
function iu(e, t) {
  try {
    console.error(t.value);
  } catch (n) {
    setTimeout(function() {
      throw n;
    });
  }
}
var Ev = typeof WeakMap == "function" ? WeakMap : Map;
function Jh(e, t, n) {
  n = Tt(-1, n), n.tag = 3, n.payload = { element: null };
  var r = t.value;
  return n.callback = function() {
    Zi || (Zi = !0, gu = r), iu(e, t);
  }, n;
}
function ep(e, t, n) {
  n = Tt(-1, n), n.tag = 3;
  var r = e.type.getDerivedStateFromError;
  if (typeof r == "function") {
    var o = t.value;
    n.payload = function() {
      return r(o);
    }, n.callback = function() {
      iu(e, t);
    };
  }
  var i = e.stateNode;
  return i !== null && typeof i.componentDidCatch == "function" && (n.callback = function() {
    iu(e, t), typeof r != "function" && (tn === null ? tn = /* @__PURE__ */ new Set([this]) : tn.add(this));
    var s = t.stack;
    this.componentDidCatch(t.value, { componentStack: s !== null ? s : "" });
  }), n;
}
function Yc(e, t, n) {
  var r = e.pingCache;
  if (r === null) {
    r = e.pingCache = new Ev();
    var o = /* @__PURE__ */ new Set();
    r.set(t, o);
  } else o = r.get(t), o === void 0 && (o = /* @__PURE__ */ new Set(), r.set(t, o));
  o.has(n) || (o.add(n), e = jv.bind(null, e, t, n), t.then(e, e));
}
function Xc(e) {
  do {
    var t;
    if ((t = e.tag === 13) && (t = e.memoizedState, t = t !== null ? t.dehydrated !== null : !0), t) return e;
    e = e.return;
  } while (e !== null);
  return null;
}
function bc(e, t, n, r, o) {
  return e.mode & 1 ? (e.flags |= 65536, e.lanes = o, e) : (e === t ? e.flags |= 65536 : (e.flags |= 128, n.flags |= 131072, n.flags &= -52805, n.tag === 1 && (n.alternate === null ? n.tag = 17 : (t = Tt(-1, 1), t.tag = 2, en(n, t, 1))), n.lanes |= 1), e);
}
var kv = jt.ReactCurrentOwner, Oe = !1;
function Ae(e, t, n, r) {
  t.child = e === null ? Ih(t, null, n, r) : ur(t, e.child, n, r);
}
function Qc(e, t, n, r, o) {
  n = n.render;
  var i = t.ref;
  return tr(t, o), r = ha(e, t, n, r, i, o), n = pa(), e !== null && !Oe ? (t.updateQueue = e.updateQueue, t.flags &= -2053, e.lanes &= ~o, Dt(e, t, o)) : (fe && n && ta(t), t.flags |= 1, Ae(e, t, r, o), t.child);
}
function Kc(e, t, n, r, o) {
  if (e === null) {
    var i = n.type;
    return typeof i == "function" && !ka(i) && i.defaultProps === void 0 && n.compare === null && n.defaultProps === void 0 ? (t.tag = 15, t.type = i, tp(e, t, i, r, o)) : (e = Ni(n.type, null, r, t, t.mode, o), e.ref = t.ref, e.return = t, t.child = e);
  }
  if (i = e.child, !(e.lanes & o)) {
    var s = i.memoizedProps;
    if (n = n.compare, n = n !== null ? n : uo, n(s, r) && e.ref === t.ref) return Dt(e, t, o);
  }
  return t.flags |= 1, e = rn(i, r), e.ref = t.ref, e.return = t, t.child = e;
}
function tp(e, t, n, r, o) {
  if (e !== null) {
    var i = e.memoizedProps;
    if (uo(i, r) && e.ref === t.ref) if (Oe = !1, t.pendingProps = r = i, (e.lanes & o) !== 0) e.flags & 131072 && (Oe = !0);
    else return t.lanes = e.lanes, Dt(e, t, o);
  }
  return su(e, t, n, r, o);
}
function np(e, t, n) {
  var r = t.pendingProps, o = r.children, i = e !== null ? e.memoizedState : null;
  if (r.mode === "hidden") if (!(t.mode & 1)) t.memoizedState = { baseLanes: 0, cachePool: null, transitions: null }, le(Qn, We), We |= n;
  else {
    if (!(n & 1073741824)) return e = i !== null ? i.baseLanes | n : n, t.lanes = t.childLanes = 1073741824, t.memoizedState = { baseLanes: e, cachePool: null, transitions: null }, t.updateQueue = null, le(Qn, We), We |= e, null;
    t.memoizedState = { baseLanes: 0, cachePool: null, transitions: null }, r = i !== null ? i.baseLanes : n, le(Qn, We), We |= r;
  }
  else i !== null ? (r = i.baseLanes | n, t.memoizedState = null) : r = n, le(Qn, We), We |= r;
  return Ae(e, t, o, n), t.child;
}
function rp(e, t) {
  var n = t.ref;
  (e === null && n !== null || e !== null && e.ref !== n) && (t.flags |= 512, t.flags |= 2097152);
}
function su(e, t, n, r, o) {
  var i = Ve(n) ? En : Le.current;
  return i = sr(t, i), tr(t, o), n = ha(e, t, n, r, i, o), r = pa(), e !== null && !Oe ? (t.updateQueue = e.updateQueue, t.flags &= -2053, e.lanes &= ~o, Dt(e, t, o)) : (fe && r && ta(t), t.flags |= 1, Ae(e, t, n, o), t.child);
}
function Gc(e, t, n, r, o) {
  if (Ve(n)) {
    var i = !0;
    Vi(t);
  } else i = !1;
  if (tr(t, o), t.stateNode === null) _i(e, t), qh(t, n, r), ou(t, n, r, o), r = !0;
  else if (e === null) {
    var s = t.stateNode, l = t.memoizedProps;
    s.props = l;
    var u = s.context, a = n.contextType;
    typeof a == "object" && a !== null ? a = tt(a) : (a = Ve(n) ? En : Le.current, a = sr(t, a));
    var d = n.getDerivedStateFromProps, c = typeof d == "function" || typeof s.getSnapshotBeforeUpdate == "function";
    c || typeof s.UNSAFE_componentWillReceiveProps != "function" && typeof s.componentWillReceiveProps != "function" || (l !== r || u !== a) && Uc(t, s, r, a), Ut = !1;
    var f = t.memoizedState;
    s.state = f, Xi(t, r, s, o), u = t.memoizedState, l !== r || f !== u || He.current || Ut ? (typeof d == "function" && (ru(t, n, d, r), u = t.memoizedState), (l = Ut || Wc(t, n, l, r, f, u, a)) ? (c || typeof s.UNSAFE_componentWillMount != "function" && typeof s.componentWillMount != "function" || (typeof s.componentWillMount == "function" && s.componentWillMount(), typeof s.UNSAFE_componentWillMount == "function" && s.UNSAFE_componentWillMount()), typeof s.componentDidMount == "function" && (t.flags |= 4194308)) : (typeof s.componentDidMount == "function" && (t.flags |= 4194308), t.memoizedProps = r, t.memoizedState = u), s.props = r, s.state = u, s.context = a, r = l) : (typeof s.componentDidMount == "function" && (t.flags |= 4194308), r = !1);
  } else {
    s = t.stateNode, Th(e, t), l = t.memoizedProps, a = t.type === t.elementType ? l : ot(t.type, l), s.props = a, c = t.pendingProps, f = s.context, u = n.contextType, typeof u == "object" && u !== null ? u = tt(u) : (u = Ve(n) ? En : Le.current, u = sr(t, u));
    var m = n.getDerivedStateFromProps;
    (d = typeof m == "function" || typeof s.getSnapshotBeforeUpdate == "function") || typeof s.UNSAFE_componentWillReceiveProps != "function" && typeof s.componentWillReceiveProps != "function" || (l !== c || f !== u) && Uc(t, s, r, u), Ut = !1, f = t.memoizedState, s.state = f, Xi(t, r, s, o);
    var y = t.memoizedState;
    l !== c || f !== y || He.current || Ut ? (typeof m == "function" && (ru(t, n, m, r), y = t.memoizedState), (a = Ut || Wc(t, n, a, r, f, y, u) || !1) ? (d || typeof s.UNSAFE_componentWillUpdate != "function" && typeof s.componentWillUpdate != "function" || (typeof s.componentWillUpdate == "function" && s.componentWillUpdate(r, y, u), typeof s.UNSAFE_componentWillUpdate == "function" && s.UNSAFE_componentWillUpdate(r, y, u)), typeof s.componentDidUpdate == "function" && (t.flags |= 4), typeof s.getSnapshotBeforeUpdate == "function" && (t.flags |= 1024)) : (typeof s.componentDidUpdate != "function" || l === e.memoizedProps && f === e.memoizedState || (t.flags |= 4), typeof s.getSnapshotBeforeUpdate != "function" || l === e.memoizedProps && f === e.memoizedState || (t.flags |= 1024), t.memoizedProps = r, t.memoizedState = y), s.props = r, s.state = y, s.context = u, r = a) : (typeof s.componentDidUpdate != "function" || l === e.memoizedProps && f === e.memoizedState || (t.flags |= 4), typeof s.getSnapshotBeforeUpdate != "function" || l === e.memoizedProps && f === e.memoizedState || (t.flags |= 1024), r = !1);
  }
  return lu(e, t, n, r, i, o);
}
function lu(e, t, n, r, o, i) {
  rp(e, t);
  var s = (t.flags & 128) !== 0;
  if (!r && !s) return o && Dc(t, n, !1), Dt(e, t, i);
  r = t.stateNode, kv.current = t;
  var l = s && typeof n.getDerivedStateFromError != "function" ? null : r.render();
  return t.flags |= 1, e !== null && s ? (t.child = ur(t, e.child, null, i), t.child = ur(t, null, l, i)) : Ae(e, t, l, i), t.memoizedState = r.state, o && Dc(t, n, !0), t.child;
}
function op(e) {
  var t = e.stateNode;
  t.pendingContext ? $c(e, t.pendingContext, t.pendingContext !== t.context) : t.context && $c(e, t.context, !1), aa(e, t.containerInfo);
}
function Zc(e, t, n, r, o) {
  return lr(), ra(o), t.flags |= 256, Ae(e, t, n, r), t.child;
}
var uu = { dehydrated: null, treeContext: null, retryLane: 0 };
function au(e) {
  return { baseLanes: e, cachePool: null, transitions: null };
}
function ip(e, t, n) {
  var r = t.pendingProps, o = pe.current, i = !1, s = (t.flags & 128) !== 0, l;
  if ((l = s) || (l = e !== null && e.memoizedState === null ? !1 : (o & 2) !== 0), l ? (i = !0, t.flags &= -129) : (e === null || e.memoizedState !== null) && (o |= 1), le(pe, o & 1), e === null)
    return tu(t), e = t.memoizedState, e !== null && (e = e.dehydrated, e !== null) ? (t.mode & 1 ? e.data === "$!" ? t.lanes = 8 : t.lanes = 1073741824 : t.lanes = 1, null) : (s = r.children, e = r.fallback, i ? (r = t.mode, i = t.child, s = { mode: "hidden", children: s }, !(r & 1) && i !== null ? (i.childLanes = 0, i.pendingProps = s) : i = Es(s, r, 0, null), e = xn(e, r, n, null), i.return = t, e.return = t, i.sibling = e, t.child = i, t.child.memoizedState = au(n), t.memoizedState = uu, e) : ya(t, s));
  if (o = e.memoizedState, o !== null && (l = o.dehydrated, l !== null)) return Nv(e, t, s, r, l, o, n);
  if (i) {
    i = r.fallback, s = t.mode, o = e.child, l = o.sibling;
    var u = { mode: "hidden", children: r.children };
    return !(s & 1) && t.child !== o ? (r = t.child, r.childLanes = 0, r.pendingProps = u, t.deletions = null) : (r = rn(o, u), r.subtreeFlags = o.subtreeFlags & 14680064), l !== null ? i = rn(l, i) : (i = xn(i, s, n, null), i.flags |= 2), i.return = t, r.return = t, r.sibling = i, t.child = r, r = i, i = t.child, s = e.child.memoizedState, s = s === null ? au(n) : { baseLanes: s.baseLanes | n, cachePool: null, transitions: s.transitions }, i.memoizedState = s, i.childLanes = e.childLanes & ~n, t.memoizedState = uu, r;
  }
  return i = e.child, e = i.sibling, r = rn(i, { mode: "visible", children: r.children }), !(t.mode & 1) && (r.lanes = n), r.return = t, r.sibling = null, e !== null && (n = t.deletions, n === null ? (t.deletions = [e], t.flags |= 16) : n.push(e)), t.child = r, t.memoizedState = null, r;
}
function ya(e, t) {
  return t = Es({ mode: "visible", children: t }, e.mode, 0, null), t.return = e, e.child = t;
}
function ti(e, t, n, r) {
  return r !== null && ra(r), ur(t, e.child, null, n), e = ya(t, t.pendingProps.children), e.flags |= 2, t.memoizedState = null, e;
}
function Nv(e, t, n, r, o, i, s) {
  if (n)
    return t.flags & 256 ? (t.flags &= -257, r = hl(Error(V(422))), ti(e, t, s, r)) : t.memoizedState !== null ? (t.child = e.child, t.flags |= 128, null) : (i = r.fallback, o = t.mode, r = Es({ mode: "visible", children: r.children }, o, 0, null), i = xn(i, o, s, null), i.flags |= 2, r.return = t, i.return = t, r.sibling = i, t.child = r, t.mode & 1 && ur(t, e.child, null, s), t.child.memoizedState = au(s), t.memoizedState = uu, i);
  if (!(t.mode & 1)) return ti(e, t, s, null);
  if (o.data === "$!") {
    if (r = o.nextSibling && o.nextSibling.dataset, r) var l = r.dgst;
    return r = l, i = Error(V(419)), r = hl(i, r, void 0), ti(e, t, s, r);
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
    return Ea(), r = hl(Error(V(421))), ti(e, t, s, r);
  }
  return o.data === "$?" ? (t.flags |= 128, t.child = e.child, t = Ov.bind(null, e), o._reactRetry = t, null) : (e = i.treeContext, Ye = Jt(o.nextSibling), Xe = t, fe = !0, lt = null, e !== null && (Ze[qe++] = Pt, Ze[qe++] = It, Ze[qe++] = kn, Pt = e.id, It = e.overflow, kn = t), t = ya(t, r.children), t.flags |= 4096, t);
}
function qc(e, t, n) {
  e.lanes |= t;
  var r = e.alternate;
  r !== null && (r.lanes |= t), nu(e.return, t, n);
}
function pl(e, t, n, r, o) {
  var i = e.memoizedState;
  i === null ? e.memoizedState = { isBackwards: t, rendering: null, renderingStartTime: 0, last: r, tail: n, tailMode: o } : (i.isBackwards = t, i.rendering = null, i.renderingStartTime = 0, i.last = r, i.tail = n, i.tailMode = o);
}
function sp(e, t, n) {
  var r = t.pendingProps, o = r.revealOrder, i = r.tail;
  if (Ae(e, t, r.children, n), r = pe.current, r & 2) r = r & 1 | 2, t.flags |= 128;
  else {
    if (e !== null && e.flags & 128) e: for (e = t.child; e !== null; ) {
      if (e.tag === 13) e.memoizedState !== null && qc(e, n, t);
      else if (e.tag === 19) qc(e, n, t);
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
      for (n = t.child, o = null; n !== null; ) e = n.alternate, e !== null && bi(e) === null && (o = n), n = n.sibling;
      n = o, n === null ? (o = t.child, t.child = null) : (o = n.sibling, n.sibling = null), pl(t, !1, o, n, i);
      break;
    case "backwards":
      for (n = null, o = t.child, t.child = null; o !== null; ) {
        if (e = o.alternate, e !== null && bi(e) === null) {
          t.child = o;
          break;
        }
        e = o.sibling, o.sibling = n, n = o, o = e;
      }
      pl(t, !0, n, null, i);
      break;
    case "together":
      pl(t, !1, null, null, void 0);
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
function Cv(e, t, n) {
  switch (t.tag) {
    case 3:
      op(t), lr();
      break;
    case 5:
      Lh(t);
      break;
    case 1:
      Ve(t.type) && Vi(t);
      break;
    case 4:
      aa(t, t.stateNode.containerInfo);
      break;
    case 10:
      var r = t.type._context, o = t.memoizedProps.value;
      le(Ui, r._currentValue), r._currentValue = o;
      break;
    case 13:
      if (r = t.memoizedState, r !== null)
        return r.dehydrated !== null ? (le(pe, pe.current & 1), t.flags |= 128, null) : n & t.child.childLanes ? ip(e, t, n) : (le(pe, pe.current & 1), e = Dt(e, t, n), e !== null ? e.sibling : null);
      le(pe, pe.current & 1);
      break;
    case 19:
      if (r = (n & t.childLanes) !== 0, e.flags & 128) {
        if (r) return sp(e, t, n);
        t.flags |= 128;
      }
      if (o = t.memoizedState, o !== null && (o.rendering = null, o.tail = null, o.lastEffect = null), le(pe, pe.current), r) break;
      return null;
    case 22:
    case 23:
      return t.lanes = 0, np(e, t, n);
  }
  return Dt(e, t, n);
}
var lp, cu, up, ap;
lp = function(e, t) {
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
cu = function() {
};
up = function(e, t, n, r) {
  var o = e.memoizedProps;
  if (o !== r) {
    e = t.stateNode, yn(xt.current);
    var i = null;
    switch (n) {
      case "input":
        o = Ll(e, o), r = Ll(e, r), i = [];
        break;
      case "select":
        o = me({}, o, { value: void 0 }), r = me({}, r, { value: void 0 }), i = [];
        break;
      case "textarea":
        o = Dl(e, o), r = Dl(e, r), i = [];
        break;
      default:
        typeof o.onClick != "function" && typeof r.onClick == "function" && (e.onclick = Fi);
    }
    jl(n, r);
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
ap = function(e, t, n, r) {
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
function ze(e) {
  var t = e.alternate !== null && e.alternate.child === e.child, n = 0, r = 0;
  if (t) for (var o = e.child; o !== null; ) n |= o.lanes | o.childLanes, r |= o.subtreeFlags & 14680064, r |= o.flags & 14680064, o.return = e, o = o.sibling;
  else for (o = e.child; o !== null; ) n |= o.lanes | o.childLanes, r |= o.subtreeFlags, r |= o.flags, o.return = e, o = o.sibling;
  return e.subtreeFlags |= r, e.childLanes = n, t;
}
function Mv(e, t, n) {
  var r = t.pendingProps;
  switch (na(t), t.tag) {
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
      return ze(t), null;
    case 1:
      return Ve(t.type) && Hi(), ze(t), null;
    case 3:
      return r = t.stateNode, ar(), ce(He), ce(Le), fa(), r.pendingContext && (r.context = r.pendingContext, r.pendingContext = null), (e === null || e.child === null) && (Jo(t) ? t.flags |= 4 : e === null || e.memoizedState.isDehydrated && !(t.flags & 256) || (t.flags |= 1024, lt !== null && (vu(lt), lt = null))), cu(e, t), ze(t), null;
    case 5:
      ca(t);
      var o = yn(po.current);
      if (n = t.type, e !== null && t.stateNode != null) up(e, t, n, r, o), e.ref !== t.ref && (t.flags |= 512, t.flags |= 2097152);
      else {
        if (!r) {
          if (t.stateNode === null) throw Error(V(166));
          return ze(t), null;
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
              lc(r, i), ae("invalid", r);
              break;
            case "select":
              r._wrapperState = { wasMultiple: !!i.multiple }, ae("invalid", r);
              break;
            case "textarea":
              ac(r, i), ae("invalid", r);
          }
          jl(n, i), o = null;
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
              Uo(r), uc(r, i, !0);
              break;
            case "textarea":
              Uo(r), cc(r);
              break;
            case "select":
            case "option":
              break;
            default:
              typeof i.onClick == "function" && (r.onclick = Fi);
          }
          r = o, t.updateQueue = r, r !== null && (t.flags |= 4);
        } else {
          s = o.nodeType === 9 ? o : o.ownerDocument, e === "http://www.w3.org/1999/xhtml" && (e = jd(n)), e === "http://www.w3.org/1999/xhtml" ? n === "script" ? (e = s.createElement("div"), e.innerHTML = "<script><\/script>", e = e.removeChild(e.firstChild)) : typeof r.is == "string" ? e = s.createElement(n, { is: r.is }) : (e = s.createElement(n), n === "select" && (s = e, r.multiple ? s.multiple = !0 : r.size && (s.size = r.size))) : e = s.createElementNS(e, n), e[vt] = t, e[fo] = r, lp(e, t, !1, !1), t.stateNode = e;
          e: {
            switch (s = Ol(n, r), n) {
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
                lc(e, r), o = Ll(e, r), ae("invalid", e);
                break;
              case "option":
                o = r;
                break;
              case "select":
                e._wrapperState = { wasMultiple: !!r.multiple }, o = me({}, r, { value: void 0 }), ae("invalid", e);
                break;
              case "textarea":
                ac(e, r), o = Dl(e, r), ae("invalid", e);
                break;
              default:
                o = r;
            }
            jl(n, o), l = o;
            for (i in l) if (l.hasOwnProperty(i)) {
              var u = l[i];
              i === "style" ? Hd(e, u) : i === "dangerouslySetInnerHTML" ? (u = u ? u.__html : void 0, u != null && Od(e, u)) : i === "children" ? typeof u == "string" ? (n !== "textarea" || u !== "") && no(e, u) : typeof u == "number" && no(e, "" + u) : i !== "suppressContentEditableWarning" && i !== "suppressHydrationWarning" && i !== "autoFocus" && (to.hasOwnProperty(i) ? u != null && i === "onScroll" && ae("scroll", e) : u != null && Vu(e, i, u, s));
            }
            switch (n) {
              case "input":
                Uo(e), uc(e, r, !1);
                break;
              case "textarea":
                Uo(e), cc(e);
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
      return ze(t), null;
    case 6:
      if (e && t.stateNode != null) ap(e, t, e.memoizedProps, r);
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
      return ze(t), null;
    case 13:
      if (ce(pe), r = t.memoizedState, e === null || e.memoizedState !== null && e.memoizedState.dehydrated !== null) {
        if (fe && Ye !== null && t.mode & 1 && !(t.flags & 128)) Mh(), lr(), t.flags |= 98560, i = !1;
        else if (i = Jo(t), r !== null && r.dehydrated !== null) {
          if (e === null) {
            if (!i) throw Error(V(318));
            if (i = t.memoizedState, i = i !== null ? i.dehydrated : null, !i) throw Error(V(317));
            i[vt] = t;
          } else lr(), !(t.flags & 128) && (t.memoizedState = null), t.flags |= 4;
          ze(t), i = !1;
        } else lt !== null && (vu(lt), lt = null), i = !0;
        if (!i) return t.flags & 65536 ? t : null;
      }
      return t.flags & 128 ? (t.lanes = n, t) : (r = r !== null, r !== (e !== null && e.memoizedState !== null) && r && (t.child.flags |= 8192, t.mode & 1 && (e === null || pe.current & 1 ? _e === 0 && (_e = 3) : Ea())), t.updateQueue !== null && (t.flags |= 4), ze(t), null);
    case 4:
      return ar(), cu(e, t), e === null && ao(t.stateNode.containerInfo), ze(t), null;
    case 10:
      return sa(t.type._context), ze(t), null;
    case 17:
      return Ve(t.type) && Hi(), ze(t), null;
    case 19:
      if (ce(pe), i = t.memoizedState, i === null) return ze(t), null;
      if (r = (t.flags & 128) !== 0, s = i.rendering, s === null) if (r) $r(i, !1);
      else {
        if (_e !== 0 || e !== null && e.flags & 128) for (e = t.child; e !== null; ) {
          if (s = bi(e), s !== null) {
            for (t.flags |= 128, $r(i, !1), r = s.updateQueue, r !== null && (t.updateQueue = r, t.flags |= 4), t.subtreeFlags = 0, r = n, n = t.child; n !== null; ) i = n, e = r, i.flags &= 14680066, s = i.alternate, s === null ? (i.childLanes = 0, i.lanes = e, i.child = null, i.subtreeFlags = 0, i.memoizedProps = null, i.memoizedState = null, i.updateQueue = null, i.dependencies = null, i.stateNode = null) : (i.childLanes = s.childLanes, i.lanes = s.lanes, i.child = s.child, i.subtreeFlags = 0, i.deletions = null, i.memoizedProps = s.memoizedProps, i.memoizedState = s.memoizedState, i.updateQueue = s.updateQueue, i.type = s.type, e = s.dependencies, i.dependencies = e === null ? null : { lanes: e.lanes, firstContext: e.firstContext }), n = n.sibling;
            return le(pe, pe.current & 1 | 2), t.child;
          }
          e = e.sibling;
        }
        i.tail !== null && ve() > fr && (t.flags |= 128, r = !0, $r(i, !1), t.lanes = 4194304);
      }
      else {
        if (!r) if (e = bi(s), e !== null) {
          if (t.flags |= 128, r = !0, n = e.updateQueue, n !== null && (t.updateQueue = n, t.flags |= 4), $r(i, !0), i.tail === null && i.tailMode === "hidden" && !s.alternate && !fe) return ze(t), null;
        } else 2 * ve() - i.renderingStartTime > fr && n !== 1073741824 && (t.flags |= 128, r = !0, $r(i, !1), t.lanes = 4194304);
        i.isBackwards ? (s.sibling = t.child, t.child = s) : (n = i.last, n !== null ? n.sibling = s : t.child = s, i.last = s);
      }
      return i.tail !== null ? (t = i.tail, i.rendering = t, i.tail = t.sibling, i.renderingStartTime = ve(), t.sibling = null, n = pe.current, le(pe, r ? n & 1 | 2 : n & 1), t) : (ze(t), null);
    case 22:
    case 23:
      return _a(), r = t.memoizedState !== null, e !== null && e.memoizedState !== null !== r && (t.flags |= 8192), r && t.mode & 1 ? We & 1073741824 && (ze(t), t.subtreeFlags & 6 && (t.flags |= 8192)) : ze(t), null;
    case 24:
      return null;
    case 25:
      return null;
  }
  throw Error(V(156, t.tag));
}
function Pv(e, t) {
  switch (na(t), t.tag) {
    case 1:
      return Ve(t.type) && Hi(), e = t.flags, e & 65536 ? (t.flags = e & -65537 | 128, t) : null;
    case 3:
      return ar(), ce(He), ce(Le), fa(), e = t.flags, e & 65536 && !(e & 128) ? (t.flags = e & -65537 | 128, t) : null;
    case 5:
      return ca(t), null;
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
      return sa(t.type._context), null;
    case 22:
    case 23:
      return _a(), null;
    case 24:
      return null;
    default:
      return null;
  }
}
var ni = !1, Te = !1, Iv = typeof WeakSet == "function" ? WeakSet : Set, Y = null;
function bn(e, t) {
  var n = e.ref;
  if (n !== null) if (typeof n == "function") try {
    n(null);
  } catch (r) {
    ye(e, t, r);
  }
  else n.current = null;
}
function fu(e, t, n) {
  try {
    n();
  } catch (r) {
    ye(e, t, r);
  }
}
var Jc = !1;
function zv(e, t) {
  if (Ql = Ri, e = ph(), ea(e)) {
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
  for (Kl = { focusedElem: e, selectionRange: n }, Ri = !1, Y = t; Y !== null; ) if (t = Y, e = t.child, (t.subtreeFlags & 1028) !== 0 && e !== null) e.return = t, Y = e;
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
  return y = Jc, Jc = !1, y;
}
function Gr(e, t, n) {
  var r = t.updateQueue;
  if (r = r !== null ? r.lastEffect : null, r !== null) {
    var o = r = r.next;
    do {
      if ((o.tag & e) === e) {
        var i = o.destroy;
        o.destroy = void 0, i !== void 0 && fu(t, n, i);
      }
      o = o.next;
    } while (o !== r);
  }
}
function Ss(e, t) {
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
function du(e) {
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
function cp(e) {
  var t = e.alternate;
  t !== null && (e.alternate = null, cp(t)), e.child = null, e.deletions = null, e.sibling = null, e.tag === 5 && (t = e.stateNode, t !== null && (delete t[vt], delete t[fo], delete t[ql], delete t[dv], delete t[hv])), e.stateNode = null, e.return = null, e.dependencies = null, e.memoizedProps = null, e.memoizedState = null, e.pendingProps = null, e.stateNode = null, e.updateQueue = null;
}
function fp(e) {
  return e.tag === 5 || e.tag === 3 || e.tag === 4;
}
function ef(e) {
  e: for (; ; ) {
    for (; e.sibling === null; ) {
      if (e.return === null || fp(e.return)) return null;
      e = e.return;
    }
    for (e.sibling.return = e.return, e = e.sibling; e.tag !== 5 && e.tag !== 6 && e.tag !== 18; ) {
      if (e.flags & 2 || e.child === null || e.tag === 4) continue e;
      e.child.return = e, e = e.child;
    }
    if (!(e.flags & 2)) return e.stateNode;
  }
}
function hu(e, t, n) {
  var r = e.tag;
  if (r === 5 || r === 6) e = e.stateNode, t ? n.nodeType === 8 ? n.parentNode.insertBefore(e, t) : n.insertBefore(e, t) : (n.nodeType === 8 ? (t = n.parentNode, t.insertBefore(e, n)) : (t = n, t.appendChild(e)), n = n._reactRootContainer, n != null || t.onclick !== null || (t.onclick = Fi));
  else if (r !== 4 && (e = e.child, e !== null)) for (hu(e, t, n), e = e.sibling; e !== null; ) hu(e, t, n), e = e.sibling;
}
function pu(e, t, n) {
  var r = e.tag;
  if (r === 5 || r === 6) e = e.stateNode, t ? n.insertBefore(e, t) : n.appendChild(e);
  else if (r !== 4 && (e = e.child, e !== null)) for (pu(e, t, n), e = e.sibling; e !== null; ) pu(e, t, n), e = e.sibling;
}
var Ne = null, it = !1;
function Ht(e, t, n) {
  for (n = n.child; n !== null; ) dp(e, t, n), n = n.sibling;
}
function dp(e, t, n) {
  if (wt && typeof wt.onCommitFiberUnmount == "function") try {
    wt.onCommitFiberUnmount(hs, n);
  } catch {
  }
  switch (n.tag) {
    case 5:
      Te || bn(n, t);
    case 6:
      var r = Ne, o = it;
      Ne = null, Ht(e, t, n), Ne = r, it = o, Ne !== null && (it ? (e = Ne, n = n.stateNode, e.nodeType === 8 ? e.parentNode.removeChild(n) : e.removeChild(n)) : Ne.removeChild(n.stateNode));
      break;
    case 18:
      Ne !== null && (it ? (e = Ne, n = n.stateNode, e.nodeType === 8 ? ll(e.parentNode, n) : e.nodeType === 1 && ll(e, n), so(e)) : ll(Ne, n.stateNode));
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
          i = i.tag, s !== void 0 && (i & 2 || i & 4) && fu(n, t, s), o = o.next;
        } while (o !== r);
      }
      Ht(e, t, n);
      break;
    case 1:
      if (!Te && (bn(n, t), r = n.stateNode, typeof r.componentWillUnmount == "function")) try {
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
function tf(e) {
  var t = e.updateQueue;
  if (t !== null) {
    e.updateQueue = null;
    var n = e.stateNode;
    n === null && (n = e.stateNode = new Iv()), t.forEach(function(r) {
      var o = Fv.bind(null, e, r);
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
      dp(i, s, o), Ne = null, it = !1;
      var u = o.alternate;
      u !== null && (u.return = null), o.return = null;
    } catch (a) {
      ye(o, t, a);
    }
  }
  if (t.subtreeFlags & 12854) for (t = t.child; t !== null; ) hp(t, e), t = t.sibling;
}
function hp(e, t) {
  var n = e.alternate, r = e.flags;
  switch (e.tag) {
    case 0:
    case 11:
    case 14:
    case 15:
      if (rt(t, e), gt(e), r & 4) {
        try {
          Gr(3, e, e.return), Ss(3, e);
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
      rt(t, e), gt(e), r & 512 && n !== null && bn(n, n.return);
      break;
    case 5:
      if (rt(t, e), gt(e), r & 512 && n !== null && bn(n, n.return), e.flags & 32) {
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
          l === "input" && i.type === "radio" && i.name != null && Dd(o, i), Ol(l, s);
          var a = Ol(l, i);
          for (s = 0; s < u.length; s += 2) {
            var d = u[s], c = u[s + 1];
            d === "style" ? Hd(o, c) : d === "dangerouslySetInnerHTML" ? Od(o, c) : d === "children" ? no(o, c) : Vu(o, d, c, a);
          }
          switch (l) {
            case "input":
              Al(o, i);
              break;
            case "textarea":
              Rd(o, i);
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
      rt(t, e), gt(e), o = e.child, o.flags & 8192 && (i = o.memoizedState !== null, o.stateNode.isHidden = i, !i || o.alternate !== null && o.alternate.memoizedState !== null || (xa = ve())), r & 4 && tf(e);
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
                bn(f, f.return);
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
                bn(f, f.return);
                break;
              case 22:
                if (f.memoizedState !== null) {
                  rf(c);
                  continue;
                }
            }
            m !== null ? (m.return = f, Y = m) : rf(c);
          }
          d = d.sibling;
        }
        e: for (d = null, c = e; ; ) {
          if (c.tag === 5) {
            if (d === null) {
              d = c;
              try {
                o = c.stateNode, a ? (i = o.style, typeof i.setProperty == "function" ? i.setProperty("display", "none", "important") : i.display = "none") : (l = c.stateNode, u = c.memoizedProps.style, s = u != null && u.hasOwnProperty("display") ? u.display : null, l.style.display = Fd("display", s));
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
      rt(t, e), gt(e), r & 4 && tf(e);
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
          if (fp(n)) {
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
          var i = ef(e);
          pu(e, i, o);
          break;
        case 3:
        case 4:
          var s = r.stateNode.containerInfo, l = ef(e);
          hu(e, l, s);
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
function Tv(e, t, n) {
  Y = e, pp(e);
}
function pp(e, t, n) {
  for (var r = (e.mode & 1) !== 0; Y !== null; ) {
    var o = Y, i = o.child;
    if (o.tag === 22 && r) {
      var s = o.memoizedState !== null || ni;
      if (!s) {
        var l = o.alternate, u = l !== null && l.memoizedState !== null || Te;
        l = ni;
        var a = Te;
        if (ni = s, (Te = u) && !a) for (Y = o; Y !== null; ) s = Y, u = s.child, s.tag === 22 && s.memoizedState !== null ? of(o) : u !== null ? (u.return = s, Y = u) : of(o);
        for (; i !== null; ) Y = i, pp(i), i = i.sibling;
        Y = o, ni = l, Te = a;
      }
      nf(e);
    } else o.subtreeFlags & 8772 && i !== null ? (i.return = o, Y = i) : nf(e);
  }
}
function nf(e) {
  for (; Y !== null; ) {
    var t = Y;
    if (t.flags & 8772) {
      var n = t.alternate;
      try {
        if (t.flags & 8772) switch (t.tag) {
          case 0:
          case 11:
          case 15:
            Te || Ss(5, t);
            break;
          case 1:
            var r = t.stateNode;
            if (t.flags & 4 && !Te) if (n === null) r.componentDidMount();
            else {
              var o = t.elementType === t.type ? n.memoizedProps : ot(t.type, n.memoizedProps);
              r.componentDidUpdate(o, n.memoizedState, r.__reactInternalSnapshotBeforeUpdate);
            }
            var i = t.updateQueue;
            i !== null && Hc(t, i, r);
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
              Hc(t, s, n);
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
        Te || t.flags & 512 && du(t);
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
function rf(e) {
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
function of(e) {
  for (; Y !== null; ) {
    var t = Y;
    try {
      switch (t.tag) {
        case 0:
        case 11:
        case 15:
          var n = t.return;
          try {
            Ss(4, t);
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
            du(t);
          } catch (u) {
            ye(t, i, u);
          }
          break;
        case 5:
          var s = t.return;
          try {
            du(t);
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
var Lv = Math.ceil, Gi = jt.ReactCurrentDispatcher, va = jt.ReactCurrentOwner, et = jt.ReactCurrentBatchConfig, re = 0, ke = null, we = null, Ce = 0, We = 0, Qn = an(0), _e = 0, vo = null, Cn = 0, _s = 0, wa = 0, Zr = null, je = null, xa = 0, fr = 1 / 0, Ct = null, Zi = !1, gu = null, tn = null, ri = !1, Kt = null, qi = 0, qr = 0, mu = null, Ei = -1, ki = 0;
function $e() {
  return re & 6 ? ve() : Ei !== -1 ? Ei : Ei = ve();
}
function nn(e) {
  return e.mode & 1 ? re & 2 && Ce !== 0 ? Ce & -Ce : gv.transition !== null ? (ki === 0 && (ki = qd()), ki) : (e = se, e !== 0 || (e = window.event, e = e === void 0 ? 16 : ih(e.type)), e) : 1;
}
function dt(e, t, n, r) {
  if (50 < qr) throw qr = 0, mu = null, Error(V(185));
  zo(e, n, r), (!(re & 2) || e !== ke) && (e === ke && (!(re & 2) && (_s |= n), _e === 4 && Xt(e, Ce)), Be(e, r), n === 1 && re === 0 && !(t.mode & 1) && (fr = ve() + 500, vs && cn()));
}
function Be(e, t) {
  var n = e.callbackNode;
  gy(e, t);
  var r = Di(e, e === ke ? Ce : 0);
  if (r === 0) n !== null && hc(n), e.callbackNode = null, e.callbackPriority = 0;
  else if (t = r & -r, e.callbackPriority !== t) {
    if (n != null && hc(n), t === 1) e.tag === 0 ? pv(sf.bind(null, e)) : kh(sf.bind(null, e)), cv(function() {
      !(re & 6) && cn();
    }), n = null;
    else {
      switch (Jd(r)) {
        case 1:
          n = Xu;
          break;
        case 4:
          n = Gd;
          break;
        case 16:
          n = $i;
          break;
        case 536870912:
          n = Zd;
          break;
        default:
          n = $i;
      }
      n = _p(n, gp.bind(null, e));
    }
    e.callbackPriority = t, e.callbackNode = n;
  }
}
function gp(e, t) {
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
    var i = yp();
    (ke !== e || Ce !== t) && (Ct = null, fr = ve() + 500, wn(e, t));
    do
      try {
        Dv();
        break;
      } catch (l) {
        mp(e, l);
      }
    while (!0);
    ia(), Gi.current = i, re = o, we !== null ? t = 0 : (ke = null, Ce = 0, t = _e);
  }
  if (t !== 0) {
    if (t === 2 && (o = Wl(e), o !== 0 && (r = o, t = yu(e, o))), t === 1) throw n = vo, wn(e, 0), Xt(e, r), Be(e, ve()), n;
    if (t === 6) Xt(e, r);
    else {
      if (o = e.current.alternate, !(r & 30) && !Av(o) && (t = Ji(e, r), t === 2 && (i = Wl(e), i !== 0 && (r = i, t = yu(e, i))), t === 1)) throw n = vo, wn(e, 0), Xt(e, r), Be(e, ve()), n;
      switch (e.finishedWork = o, e.finishedLanes = r, t) {
        case 0:
        case 1:
          throw Error(V(345));
        case 2:
          hn(e, je, Ct);
          break;
        case 3:
          if (Xt(e, r), (r & 130023424) === r && (t = xa + 500 - ve(), 10 < t)) {
            if (Di(e, 0) !== 0) break;
            if (o = e.suspendedLanes, (o & r) !== r) {
              $e(), e.pingedLanes |= e.suspendedLanes & o;
              break;
            }
            e.timeoutHandle = Zl(hn.bind(null, e, je, Ct), t);
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
          if (r = o, r = ve() - r, r = (120 > r ? 120 : 480 > r ? 480 : 1080 > r ? 1080 : 1920 > r ? 1920 : 3e3 > r ? 3e3 : 4320 > r ? 4320 : 1960 * Lv(r / 1960)) - r, 10 < r) {
            e.timeoutHandle = Zl(hn.bind(null, e, je, Ct), r);
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
  return Be(e, ve()), e.callbackNode === n ? gp.bind(null, e) : null;
}
function yu(e, t) {
  var n = Zr;
  return e.current.memoizedState.isDehydrated && (wn(e, t).flags |= 256), e = Ji(e, t), e !== 2 && (t = je, je = n, t !== null && vu(t)), e;
}
function vu(e) {
  je === null ? je = e : je.push.apply(je, e);
}
function Av(e) {
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
  for (t &= ~wa, t &= ~_s, e.suspendedLanes |= t, e.pingedLanes &= ~t, e = e.expirationTimes; 0 < t; ) {
    var n = 31 - ft(t), r = 1 << n;
    e[n] = -1, t &= ~r;
  }
}
function sf(e) {
  if (re & 6) throw Error(V(327));
  nr();
  var t = Di(e, 0);
  if (!(t & 1)) return Be(e, ve()), null;
  var n = Ji(e, t);
  if (e.tag !== 0 && n === 2) {
    var r = Wl(e);
    r !== 0 && (t = r, n = yu(e, r));
  }
  if (n === 1) throw n = vo, wn(e, 0), Xt(e, t), Be(e, ve()), n;
  if (n === 6) throw Error(V(345));
  return e.finishedWork = e.current.alternate, e.finishedLanes = t, hn(e, je, Ct), Be(e, ve()), null;
}
function Sa(e, t) {
  var n = re;
  re |= 1;
  try {
    return e(t);
  } finally {
    re = n, re === 0 && (fr = ve() + 500, vs && cn());
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
function _a() {
  We = Qn.current, ce(Qn);
}
function wn(e, t) {
  e.finishedWork = null, e.finishedLanes = 0;
  var n = e.timeoutHandle;
  if (n !== -1 && (e.timeoutHandle = -1, av(n)), we !== null) for (n = we.return; n !== null; ) {
    var r = n;
    switch (na(r), r.tag) {
      case 1:
        r = r.type.childContextTypes, r != null && Hi();
        break;
      case 3:
        ar(), ce(He), ce(Le), fa();
        break;
      case 5:
        ca(r);
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
        sa(r.type._context);
        break;
      case 22:
      case 23:
        _a();
    }
    n = n.return;
  }
  if (ke = e, we = e = rn(e.current, null), Ce = We = t, _e = 0, vo = null, wa = _s = Cn = 0, je = Zr = null, mn !== null) {
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
function mp(e, t) {
  do {
    var n = we;
    try {
      if (ia(), xi.current = Ki, Qi) {
        for (var r = ge.memoizedState; r !== null; ) {
          var o = r.queue;
          o !== null && (o.pending = null), r = r.next;
        }
        Qi = !1;
      }
      if (Nn = 0, Ee = Se = ge = null, Kr = !1, go = 0, va.current = null, n === null || n.return === null) {
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
          var m = Xc(s);
          if (m !== null) {
            m.flags &= -257, bc(m, s, l, i, t), m.mode & 1 && Yc(i, a, t), t = m, u = a;
            var y = t.updateQueue;
            if (y === null) {
              var w = /* @__PURE__ */ new Set();
              w.add(u), t.updateQueue = w;
            } else y.add(u);
            break e;
          } else {
            if (!(t & 1)) {
              Yc(i, a, t), Ea();
              break e;
            }
            u = Error(V(426));
          }
        } else if (fe && l.mode & 1) {
          var x = Xc(s);
          if (x !== null) {
            !(x.flags & 65536) && (x.flags |= 256), bc(x, s, l, i, t), ra(cr(u, l));
            break e;
          }
        }
        i = u = cr(u, l), _e !== 4 && (_e = 2), Zr === null ? Zr = [i] : Zr.push(i), i = s;
        do {
          switch (i.tag) {
            case 3:
              i.flags |= 65536, t &= -t, i.lanes |= t;
              var p = Jh(i, u, t);
              Fc(i, p);
              break e;
            case 1:
              l = u;
              var g = i.type, h = i.stateNode;
              if (!(i.flags & 128) && (typeof g.getDerivedStateFromError == "function" || h !== null && typeof h.componentDidCatch == "function" && (tn === null || !tn.has(h)))) {
                i.flags |= 65536, t &= -t, i.lanes |= t;
                var v = ep(i, l, t);
                Fc(i, v);
                break e;
              }
          }
          i = i.return;
        } while (i !== null);
      }
      wp(n);
    } catch (_) {
      t = _, we === n && n !== null && (we = n = n.return);
      continue;
    }
    break;
  } while (!0);
}
function yp() {
  var e = Gi.current;
  return Gi.current = Ki, e === null ? Ki : e;
}
function Ea() {
  (_e === 0 || _e === 3 || _e === 2) && (_e = 4), ke === null || !(Cn & 268435455) && !(_s & 268435455) || Xt(ke, Ce);
}
function Ji(e, t) {
  var n = re;
  re |= 2;
  var r = yp();
  (ke !== e || Ce !== t) && (Ct = null, wn(e, t));
  do
    try {
      $v();
      break;
    } catch (o) {
      mp(e, o);
    }
  while (!0);
  if (ia(), re = n, Gi.current = r, we !== null) throw Error(V(261));
  return ke = null, Ce = 0, _e;
}
function $v() {
  for (; we !== null; ) vp(we);
}
function Dv() {
  for (; we !== null && !sy(); ) vp(we);
}
function vp(e) {
  var t = Sp(e.alternate, e, We);
  e.memoizedProps = e.pendingProps, t === null ? wp(e) : we = t, va.current = null;
}
function wp(e) {
  var t = e;
  do {
    var n = t.alternate;
    if (e = t.return, t.flags & 32768) {
      if (n = Pv(n, t), n !== null) {
        n.flags &= 32767, we = n;
        return;
      }
      if (e !== null) e.flags |= 32768, e.subtreeFlags = 0, e.deletions = null;
      else {
        _e = 6, we = null;
        return;
      }
    } else if (n = Mv(n, t, We), n !== null) {
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
    et.transition = null, se = 1, Rv(e, t, n, r);
  } finally {
    et.transition = o, se = r;
  }
  return null;
}
function Rv(e, t, n, r) {
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
  if (my(e, i), e === ke && (we = ke = null, Ce = 0), !(n.subtreeFlags & 2064) && !(n.flags & 2064) || ri || (ri = !0, _p($i, function() {
    return nr(), null;
  })), i = (n.flags & 15990) !== 0, n.subtreeFlags & 15990 || i) {
    i = et.transition, et.transition = null;
    var s = se;
    se = 1;
    var l = re;
    re |= 4, va.current = null, zv(e, n), hp(n, e), nv(Kl), Ri = !!Ql, Kl = Ql = null, e.current = n, Tv(n), ly(), re = l, se = s, et.transition = i;
  } else e.current = n;
  if (ri && (ri = !1, Kt = e, qi = o), i = e.pendingLanes, i === 0 && (tn = null), cy(n.stateNode), Be(e, ve()), t !== null) for (r = e.onRecoverableError, n = 0; n < t.length; n++) o = t[n], r(o.value, { componentStack: o.stack, digest: o.digest });
  if (Zi) throw Zi = !1, e = gu, gu = null, e;
  return qi & 1 && e.tag !== 0 && nr(), i = e.pendingLanes, i & 1 ? e === mu ? qr++ : (qr = 0, mu = e) : qr = 0, cn(), null;
}
function nr() {
  if (Kt !== null) {
    var e = Jd(qi), t = et.transition, n = se;
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
                    if (cp(d), d === a) {
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
                  Ss(9, l);
              }
            } catch (_) {
              ye(l, l.return, _);
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
          wt.onPostCommitFiberRoot(hs, e);
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
function lf(e, t, n) {
  t = cr(n, t), t = Jh(e, t, 1), e = en(e, t, 1), t = $e(), e !== null && (zo(e, 1, t), Be(e, t));
}
function ye(e, t, n) {
  if (e.tag === 3) lf(e, e, n);
  else for (; t !== null; ) {
    if (t.tag === 3) {
      lf(t, e, n);
      break;
    } else if (t.tag === 1) {
      var r = t.stateNode;
      if (typeof t.type.getDerivedStateFromError == "function" || typeof r.componentDidCatch == "function" && (tn === null || !tn.has(r))) {
        e = cr(n, e), e = ep(t, e, 1), t = en(t, e, 1), e = $e(), t !== null && (zo(t, 1, e), Be(t, e));
        break;
      }
    }
    t = t.return;
  }
}
function jv(e, t, n) {
  var r = e.pingCache;
  r !== null && r.delete(t), t = $e(), e.pingedLanes |= e.suspendedLanes & n, ke === e && (Ce & n) === n && (_e === 4 || _e === 3 && (Ce & 130023424) === Ce && 500 > ve() - xa ? wn(e, 0) : wa |= n), Be(e, t);
}
function xp(e, t) {
  t === 0 && (e.mode & 1 ? (t = bo, bo <<= 1, !(bo & 130023424) && (bo = 4194304)) : t = 1);
  var n = $e();
  e = $t(e, t), e !== null && (zo(e, t, n), Be(e, n));
}
function Ov(e) {
  var t = e.memoizedState, n = 0;
  t !== null && (n = t.retryLane), xp(e, n);
}
function Fv(e, t) {
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
  r !== null && r.delete(t), xp(e, n);
}
var Sp;
Sp = function(e, t, n) {
  if (e !== null) if (e.memoizedProps !== t.pendingProps || He.current) Oe = !0;
  else {
    if (!(e.lanes & n) && !(t.flags & 128)) return Oe = !1, Cv(e, t, n);
    Oe = !!(e.flags & 131072);
  }
  else Oe = !1, fe && t.flags & 1048576 && Nh(t, Wi, t.index);
  switch (t.lanes = 0, t.tag) {
    case 2:
      var r = t.type;
      _i(e, t), e = t.pendingProps;
      var o = sr(t, Le.current);
      tr(t, n), o = ha(null, t, r, e, o, n);
      var i = pa();
      return t.flags |= 1, typeof o == "object" && o !== null && typeof o.render == "function" && o.$$typeof === void 0 ? (t.tag = 1, t.memoizedState = null, t.updateQueue = null, Ve(r) ? (i = !0, Vi(t)) : i = !1, t.memoizedState = o.state !== null && o.state !== void 0 ? o.state : null, ua(t), o.updater = xs, t.stateNode = o, o._reactInternals = t, ou(t, r, e, n), t = lu(null, t, r, !0, i, n)) : (t.tag = 0, fe && i && ta(t), Ae(null, t, o, n), t = t.child), t;
    case 16:
      r = t.elementType;
      e: {
        switch (_i(e, t), e = t.pendingProps, o = r._init, r = o(r._payload), t.type = r, o = t.tag = Vv(r), e = ot(r, e), o) {
          case 0:
            t = su(null, t, r, e, n);
            break e;
          case 1:
            t = Gc(null, t, r, e, n);
            break e;
          case 11:
            t = Qc(null, t, r, e, n);
            break e;
          case 14:
            t = Kc(null, t, r, ot(r.type, e), n);
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
      return r = t.type, o = t.pendingProps, o = t.elementType === r ? o : ot(r, o), su(e, t, r, o, n);
    case 1:
      return r = t.type, o = t.pendingProps, o = t.elementType === r ? o : ot(r, o), Gc(e, t, r, o, n);
    case 3:
      e: {
        if (op(t), e === null) throw Error(V(387));
        r = t.pendingProps, i = t.memoizedState, o = i.element, Th(e, t), Xi(t, r, null, n);
        var s = t.memoizedState;
        if (r = s.element, i.isDehydrated) if (i = { element: r, isDehydrated: !1, cache: s.cache, pendingSuspenseBoundaries: s.pendingSuspenseBoundaries, transitions: s.transitions }, t.updateQueue.baseState = i, t.memoizedState = i, t.flags & 256) {
          o = cr(Error(V(423)), t), t = Zc(e, t, r, n, o);
          break e;
        } else if (r !== o) {
          o = cr(Error(V(424)), t), t = Zc(e, t, r, n, o);
          break e;
        } else for (Ye = Jt(t.stateNode.containerInfo.firstChild), Xe = t, fe = !0, lt = null, n = Ih(t, null, r, n), t.child = n; n; ) n.flags = n.flags & -3 | 4096, n = n.sibling;
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
      return Lh(t), e === null && tu(t), r = t.type, o = t.pendingProps, i = e !== null ? e.memoizedProps : null, s = o.children, Gl(r, o) ? s = null : i !== null && Gl(r, i) && (t.flags |= 32), rp(e, t), Ae(e, t, s, n), t.child;
    case 6:
      return e === null && tu(t), null;
    case 13:
      return ip(e, t, n);
    case 4:
      return aa(t, t.stateNode.containerInfo), r = t.pendingProps, e === null ? t.child = ur(t, null, r, n) : Ae(e, t, r, n), t.child;
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
        if (r = t.type._context, o = t.pendingProps, i = t.memoizedProps, s = o.value, le(Ui, r._currentValue), r._currentValue = s, i !== null) if (ht(i.value, s)) {
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
                i.lanes |= n, u = i.alternate, u !== null && (u.lanes |= n), nu(
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
            s.lanes |= n, l = s.alternate, l !== null && (l.lanes |= n), nu(s, n, t), s = i.sibling;
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
      return r = t.type, o = ot(r, t.pendingProps), o = ot(r.type, o), Kc(e, t, r, o, n);
    case 15:
      return tp(e, t, t.type, t.pendingProps, n);
    case 17:
      return r = t.type, o = t.pendingProps, o = t.elementType === r ? o : ot(r, o), _i(e, t), t.tag = 1, Ve(r) ? (e = !0, Vi(t)) : e = !1, tr(t, n), qh(t, r, o), ou(t, r, o, n), lu(null, t, r, !0, e, n);
    case 19:
      return sp(e, t, n);
    case 22:
      return np(e, t, n);
  }
  throw Error(V(156, t.tag));
};
function _p(e, t) {
  return Kd(e, t);
}
function Hv(e, t, n, r) {
  this.tag = e, this.key = n, this.sibling = this.child = this.return = this.stateNode = this.type = this.elementType = null, this.index = 0, this.ref = null, this.pendingProps = t, this.dependencies = this.memoizedState = this.updateQueue = this.memoizedProps = null, this.mode = r, this.subtreeFlags = this.flags = 0, this.deletions = null, this.childLanes = this.lanes = 0, this.alternate = null;
}
function Je(e, t, n, r) {
  return new Hv(e, t, n, r);
}
function ka(e) {
  return e = e.prototype, !(!e || !e.isReactComponent);
}
function Vv(e) {
  if (typeof e == "function") return ka(e) ? 1 : 0;
  if (e != null) {
    if (e = e.$$typeof, e === Wu) return 11;
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
  if (r = e, typeof e == "function") ka(e) && (s = 1);
  else if (typeof e == "string") s = 5;
  else e: switch (e) {
    case On:
      return xn(n.children, o, i, t);
    case Bu:
      s = 8, o |= 8;
      break;
    case Pl:
      return e = Je(12, n, t, o | 2), e.elementType = Pl, e.lanes = i, e;
    case Il:
      return e = Je(13, n, t, o), e.elementType = Il, e.lanes = i, e;
    case zl:
      return e = Je(19, n, t, o), e.elementType = zl, e.lanes = i, e;
    case Ld:
      return Es(n, o, i, t);
    default:
      if (typeof e == "object" && e !== null) switch (e.$$typeof) {
        case zd:
          s = 10;
          break e;
        case Td:
          s = 9;
          break e;
        case Wu:
          s = 11;
          break e;
        case Uu:
          s = 14;
          break e;
        case Wt:
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
function Es(e, t, n, r) {
  return e = Je(22, e, r, t), e.elementType = Ld, e.lanes = n, e.stateNode = { isHidden: !1 }, e;
}
function gl(e, t, n) {
  return e = Je(6, e, null, t), e.lanes = n, e;
}
function ml(e, t, n) {
  return t = Je(4, e.children !== null ? e.children : [], e.key, t), t.lanes = n, t.stateNode = { containerInfo: e.containerInfo, pendingChildren: null, implementation: e.implementation }, t;
}
function Bv(e, t, n, r, o) {
  this.tag = t, this.containerInfo = e, this.finishedWork = this.pingCache = this.current = this.pendingChildren = null, this.timeoutHandle = -1, this.callbackNode = this.pendingContext = this.context = null, this.callbackPriority = 0, this.eventTimes = Gs(0), this.expirationTimes = Gs(-1), this.entangledLanes = this.finishedLanes = this.mutableReadLanes = this.expiredLanes = this.pingedLanes = this.suspendedLanes = this.pendingLanes = 0, this.entanglements = Gs(0), this.identifierPrefix = r, this.onRecoverableError = o, this.mutableSourceEagerHydrationData = null;
}
function Na(e, t, n, r, o, i, s, l, u) {
  return e = new Bv(e, t, n, l, u), t === 1 ? (t = 1, i === !0 && (t |= 8)) : t = 0, i = Je(3, null, null, t), e.current = i, i.stateNode = e, i.memoizedState = { element: r, isDehydrated: n, cache: null, transitions: null, pendingSuspenseBoundaries: null }, ua(i), e;
}
function Wv(e, t, n) {
  var r = 3 < arguments.length && arguments[3] !== void 0 ? arguments[3] : null;
  return { $$typeof: jn, key: r == null ? null : "" + r, children: e, containerInfo: t, implementation: n };
}
function Ep(e) {
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
    if (Ve(n)) return Eh(e, n, t);
  }
  return t;
}
function kp(e, t, n, r, o, i, s, l, u) {
  return e = Na(n, r, !0, e, o, i, s, l, u), e.context = Ep(null), n = e.current, r = $e(), o = nn(n), i = Tt(r, o), i.callback = t ?? null, en(n, i, o), e.current.lanes = o, zo(e, o, r), Be(e, r), e;
}
function ks(e, t, n, r) {
  var o = t.current, i = $e(), s = nn(o);
  return n = Ep(n), t.context === null ? t.context = n : t.pendingContext = n, t = Tt(i, s), t.payload = { element: e }, r = r === void 0 ? null : r, r !== null && (t.callback = r), e = en(o, t, s), e !== null && (dt(e, o, s, i), wi(e, o, s)), s;
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
function uf(e, t) {
  if (e = e.memoizedState, e !== null && e.dehydrated !== null) {
    var n = e.retryLane;
    e.retryLane = n !== 0 && n < t ? n : t;
  }
}
function Ca(e, t) {
  uf(e, t), (e = e.alternate) && uf(e, t);
}
function Uv() {
  return null;
}
var Np = typeof reportError == "function" ? reportError : function(e) {
  console.error(e);
};
function Ma(e) {
  this._internalRoot = e;
}
Ns.prototype.render = Ma.prototype.render = function(e) {
  var t = this._internalRoot;
  if (t === null) throw Error(V(409));
  ks(e, t, null, null);
};
Ns.prototype.unmount = Ma.prototype.unmount = function() {
  var e = this._internalRoot;
  if (e !== null) {
    this._internalRoot = null;
    var t = e.containerInfo;
    Mn(function() {
      ks(null, e, null, null);
    }), t[At] = null;
  }
};
function Ns(e) {
  this._internalRoot = e;
}
Ns.prototype.unstable_scheduleHydration = function(e) {
  if (e) {
    var t = nh();
    e = { blockedOn: null, target: e, priority: t };
    for (var n = 0; n < Yt.length && t !== 0 && t < Yt[n].priority; n++) ;
    Yt.splice(n, 0, e), n === 0 && oh(e);
  }
};
function Pa(e) {
  return !(!e || e.nodeType !== 1 && e.nodeType !== 9 && e.nodeType !== 11);
}
function Cs(e) {
  return !(!e || e.nodeType !== 1 && e.nodeType !== 9 && e.nodeType !== 11 && (e.nodeType !== 8 || e.nodeValue !== " react-mount-point-unstable "));
}
function af() {
}
function Yv(e, t, n, r, o) {
  if (o) {
    if (typeof r == "function") {
      var i = r;
      r = function() {
        var a = es(s);
        i.call(a);
      };
    }
    var s = kp(t, r, e, 0, null, !1, !1, "", af);
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
  var u = Na(e, 0, !1, null, null, !1, !1, "", af);
  return e._reactRootContainer = u, e[At] = u.current, ao(e.nodeType === 8 ? e.parentNode : e), Mn(function() {
    ks(t, u, n, r);
  }), u;
}
function Ms(e, t, n, r, o) {
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
    ks(t, s, e, o);
  } else s = Yv(n, t, e, o, r);
  return es(s);
}
eh = function(e) {
  switch (e.tag) {
    case 3:
      var t = e.stateNode;
      if (t.current.memoizedState.isDehydrated) {
        var n = Hr(t.pendingLanes);
        n !== 0 && (bu(t, n | 1), Be(t, ve()), !(re & 6) && (fr = ve() + 500, cn()));
      }
      break;
    case 13:
      Mn(function() {
        var r = $t(e, 1);
        if (r !== null) {
          var o = $e();
          dt(r, e, 1, o);
        }
      }), Ca(e, 1);
  }
};
Qu = function(e) {
  if (e.tag === 13) {
    var t = $t(e, 134217728);
    if (t !== null) {
      var n = $e();
      dt(t, e, 134217728, n);
    }
    Ca(e, 134217728);
  }
};
th = function(e) {
  if (e.tag === 13) {
    var t = nn(e), n = $t(e, t);
    if (n !== null) {
      var r = $e();
      dt(n, e, t, r);
    }
    Ca(e, t);
  }
};
nh = function() {
  return se;
};
rh = function(e, t) {
  var n = se;
  try {
    return se = e, t();
  } finally {
    se = n;
  }
};
Hl = function(e, t, n) {
  switch (t) {
    case "input":
      if (Al(e, n), t = n.name, n.type === "radio" && t != null) {
        for (n = e; n.parentNode; ) n = n.parentNode;
        for (n = n.querySelectorAll("input[name=" + JSON.stringify("" + t) + '][type="radio"]'), t = 0; t < n.length; t++) {
          var r = n[t];
          if (r !== e && r.form === e.form) {
            var o = ys(r);
            if (!o) throw Error(V(90));
            $d(r), Al(r, o);
          }
        }
      }
      break;
    case "textarea":
      Rd(e, n);
      break;
    case "select":
      t = n.value, t != null && Zn(e, !!n.multiple, t, !1);
  }
};
Wd = Sa;
Ud = Mn;
var Xv = { usingClientEntryPoint: !1, Events: [Lo, Bn, ys, Vd, Bd, Sa] }, Dr = { findFiberByHostInstance: gn, bundleType: 0, version: "18.3.1", rendererPackageName: "react-dom" }, bv = { bundleType: Dr.bundleType, version: Dr.version, rendererPackageName: Dr.rendererPackageName, rendererConfig: Dr.rendererConfig, overrideHookState: null, overrideHookStateDeletePath: null, overrideHookStateRenamePath: null, overrideProps: null, overridePropsDeletePath: null, overridePropsRenamePath: null, setErrorHandler: null, setSuspenseHandler: null, scheduleUpdate: null, currentDispatcherRef: jt.ReactCurrentDispatcher, findHostInstanceByFiber: function(e) {
  return e = bd(e), e === null ? null : e.stateNode;
}, findFiberByHostInstance: Dr.findFiberByHostInstance || Uv, findHostInstancesForRefresh: null, scheduleRefresh: null, scheduleRoot: null, setRefreshHandler: null, getCurrentFiber: null, reconcilerVersion: "18.3.1-next-f1338f8080-20240426" };
if (typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ < "u") {
  var oi = __REACT_DEVTOOLS_GLOBAL_HOOK__;
  if (!oi.isDisabled && oi.supportsFiber) try {
    hs = oi.inject(bv), wt = oi;
  } catch {
  }
}
Ke.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED = Xv;
Ke.createPortal = function(e, t) {
  var n = 2 < arguments.length && arguments[2] !== void 0 ? arguments[2] : null;
  if (!Pa(t)) throw Error(V(200));
  return Wv(e, t, null, n);
};
Ke.createRoot = function(e, t) {
  if (!Pa(e)) throw Error(V(299));
  var n = !1, r = "", o = Np;
  return t != null && (t.unstable_strictMode === !0 && (n = !0), t.identifierPrefix !== void 0 && (r = t.identifierPrefix), t.onRecoverableError !== void 0 && (o = t.onRecoverableError)), t = Na(e, 1, !1, null, null, n, !1, r, o), e[At] = t.current, ao(e.nodeType === 8 ? e.parentNode : e), new Ma(t);
};
Ke.findDOMNode = function(e) {
  if (e == null) return null;
  if (e.nodeType === 1) return e;
  var t = e._reactInternals;
  if (t === void 0)
    throw typeof e.render == "function" ? Error(V(188)) : (e = Object.keys(e).join(","), Error(V(268, e)));
  return e = bd(t), e = e === null ? null : e.stateNode, e;
};
Ke.flushSync = function(e) {
  return Mn(e);
};
Ke.hydrate = function(e, t, n) {
  if (!Cs(t)) throw Error(V(200));
  return Ms(null, e, t, !0, n);
};
Ke.hydrateRoot = function(e, t, n) {
  if (!Pa(e)) throw Error(V(405));
  var r = n != null && n.hydratedSources || null, o = !1, i = "", s = Np;
  if (n != null && (n.unstable_strictMode === !0 && (o = !0), n.identifierPrefix !== void 0 && (i = n.identifierPrefix), n.onRecoverableError !== void 0 && (s = n.onRecoverableError)), t = kp(t, null, e, 1, n ?? null, o, !1, i, s), e[At] = t.current, ao(e), r) for (e = 0; e < r.length; e++) n = r[e], o = n._getVersion, o = o(n._source), t.mutableSourceEagerHydrationData == null ? t.mutableSourceEagerHydrationData = [n, o] : t.mutableSourceEagerHydrationData.push(
    n,
    o
  );
  return new Ns(t);
};
Ke.render = function(e, t, n) {
  if (!Cs(t)) throw Error(V(200));
  return Ms(null, e, t, !1, n);
};
Ke.unmountComponentAtNode = function(e) {
  if (!Cs(e)) throw Error(V(40));
  return e._reactRootContainer ? (Mn(function() {
    Ms(null, null, e, !1, function() {
      e._reactRootContainer = null, e[At] = null;
    });
  }), !0) : !1;
};
Ke.unstable_batchedUpdates = Sa;
Ke.unstable_renderSubtreeIntoContainer = function(e, t, n, r) {
  if (!Cs(n)) throw Error(V(200));
  if (e == null || e._reactInternals === void 0) throw Error(V(38));
  return Ms(e, t, n, !1, r);
};
Ke.version = "18.3.1-next-f1338f8080-20240426";
function Cp() {
  if (!(typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ > "u" || typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE != "function"))
    try {
      __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE(Cp);
    } catch (e) {
      console.error(e);
    }
}
Cp(), Cd.exports = Ke;
var Qv = Cd.exports, Mp, cf = Qv;
Mp = cf.createRoot, cf.hydrateRoot;
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
var Kv = { value: () => {
} };
function Ps() {
  for (var e = 0, t = arguments.length, n = {}, r; e < t; ++e) {
    if (!(r = arguments[e] + "") || r in n || /[\s.]/.test(r)) throw new Error("illegal type: " + r);
    n[r] = [];
  }
  return new Ci(n);
}
function Ci(e) {
  this._ = e;
}
function Gv(e, t) {
  return e.trim().split(/^|\s+/).map(function(n) {
    var r = "", o = n.indexOf(".");
    if (o >= 0 && (r = n.slice(o + 1), n = n.slice(0, o)), n && !t.hasOwnProperty(n)) throw new Error("unknown type: " + n);
    return { type: n, name: r };
  });
}
Ci.prototype = Ps.prototype = {
  constructor: Ci,
  on: function(e, t) {
    var n = this._, r = Gv(e + "", n), o, i = -1, s = r.length;
    if (arguments.length < 2) {
      for (; ++i < s; ) if ((o = (e = r[i]).type) && (o = Zv(n[o], e.name))) return o;
      return;
    }
    if (t != null && typeof t != "function") throw new Error("invalid callback: " + t);
    for (; ++i < s; )
      if (o = (e = r[i]).type) n[o] = ff(n[o], e.name, t);
      else if (t == null) for (o in n) n[o] = ff(n[o], e.name, null);
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
function Zv(e, t) {
  for (var n = 0, r = e.length, o; n < r; ++n)
    if ((o = e[n]).name === t)
      return o.value;
}
function ff(e, t, n) {
  for (var r = 0, o = e.length; r < o; ++r)
    if (e[r].name === t) {
      e[r] = Kv, e = e.slice(0, r).concat(e.slice(r + 1));
      break;
    }
  return n != null && e.push({ name: t, value: n }), e;
}
var wu = "http://www.w3.org/1999/xhtml";
const df = {
  svg: "http://www.w3.org/2000/svg",
  xhtml: wu,
  xlink: "http://www.w3.org/1999/xlink",
  xml: "http://www.w3.org/XML/1998/namespace",
  xmlns: "http://www.w3.org/2000/xmlns/"
};
function Is(e) {
  var t = e += "", n = t.indexOf(":");
  return n >= 0 && (t = e.slice(0, n)) !== "xmlns" && (e = e.slice(n + 1)), df.hasOwnProperty(t) ? { space: df[t], local: e } : e;
}
function qv(e) {
  return function() {
    var t = this.ownerDocument, n = this.namespaceURI;
    return n === wu && t.documentElement.namespaceURI === wu ? t.createElement(e) : t.createElementNS(n, e);
  };
}
function Jv(e) {
  return function() {
    return this.ownerDocument.createElementNS(e.space, e.local);
  };
}
function Pp(e) {
  var t = Is(e);
  return (t.local ? Jv : qv)(t);
}
function ew() {
}
function Ia(e) {
  return e == null ? ew : function() {
    return this.querySelector(e);
  };
}
function tw(e) {
  typeof e != "function" && (e = Ia(e));
  for (var t = this._groups, n = t.length, r = new Array(n), o = 0; o < n; ++o)
    for (var i = t[o], s = i.length, l = r[o] = new Array(s), u, a, d = 0; d < s; ++d)
      (u = i[d]) && (a = e.call(u, u.__data__, d, i)) && ("__data__" in u && (a.__data__ = u.__data__), l[d] = a);
  return new Qe(r, this._parents);
}
function nw(e) {
  return e == null ? [] : Array.isArray(e) ? e : Array.from(e);
}
function rw() {
  return [];
}
function Ip(e) {
  return e == null ? rw : function() {
    return this.querySelectorAll(e);
  };
}
function ow(e) {
  return function() {
    return nw(e.apply(this, arguments));
  };
}
function iw(e) {
  typeof e == "function" ? e = ow(e) : e = Ip(e);
  for (var t = this._groups, n = t.length, r = [], o = [], i = 0; i < n; ++i)
    for (var s = t[i], l = s.length, u, a = 0; a < l; ++a)
      (u = s[a]) && (r.push(e.call(u, u.__data__, a, s)), o.push(u));
  return new Qe(r, o);
}
function zp(e) {
  return function() {
    return this.matches(e);
  };
}
function Tp(e) {
  return function(t) {
    return t.matches(e);
  };
}
var sw = Array.prototype.find;
function lw(e) {
  return function() {
    return sw.call(this.children, e);
  };
}
function uw() {
  return this.firstElementChild;
}
function aw(e) {
  return this.select(e == null ? uw : lw(typeof e == "function" ? e : Tp(e)));
}
var cw = Array.prototype.filter;
function fw() {
  return Array.from(this.children);
}
function dw(e) {
  return function() {
    return cw.call(this.children, e);
  };
}
function hw(e) {
  return this.selectAll(e == null ? fw : dw(typeof e == "function" ? e : Tp(e)));
}
function pw(e) {
  typeof e != "function" && (e = zp(e));
  for (var t = this._groups, n = t.length, r = new Array(n), o = 0; o < n; ++o)
    for (var i = t[o], s = i.length, l = r[o] = [], u, a = 0; a < s; ++a)
      (u = i[a]) && e.call(u, u.__data__, a, i) && l.push(u);
  return new Qe(r, this._parents);
}
function Lp(e) {
  return new Array(e.length);
}
function gw() {
  return new Qe(this._enter || this._groups.map(Lp), this._parents);
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
function mw(e) {
  return function() {
    return e;
  };
}
function yw(e, t, n, r, o, i) {
  for (var s = 0, l, u = t.length, a = i.length; s < a; ++s)
    (l = t[s]) ? (l.__data__ = i[s], r[s] = l) : n[s] = new ts(e, i[s]);
  for (; s < u; ++s)
    (l = t[s]) && (o[s] = l);
}
function vw(e, t, n, r, o, i, s) {
  var l, u, a = /* @__PURE__ */ new Map(), d = t.length, c = i.length, f = new Array(d), m;
  for (l = 0; l < d; ++l)
    (u = t[l]) && (f[l] = m = s.call(u, u.__data__, l, t) + "", a.has(m) ? o[l] = u : a.set(m, u));
  for (l = 0; l < c; ++l)
    m = s.call(e, i[l], l, i) + "", (u = a.get(m)) ? (r[l] = u, u.__data__ = i[l], a.delete(m)) : n[l] = new ts(e, i[l]);
  for (l = 0; l < d; ++l)
    (u = t[l]) && a.get(f[l]) === u && (o[l] = u);
}
function ww(e) {
  return e.__data__;
}
function xw(e, t) {
  if (!arguments.length) return Array.from(this, ww);
  var n = t ? vw : yw, r = this._parents, o = this._groups;
  typeof e != "function" && (e = mw(e));
  for (var i = o.length, s = new Array(i), l = new Array(i), u = new Array(i), a = 0; a < i; ++a) {
    var d = r[a], c = o[a], f = c.length, m = Sw(e.call(d, d && d.__data__, a, r)), y = m.length, w = l[a] = new Array(y), x = s[a] = new Array(y), p = u[a] = new Array(f);
    n(d, c, w, x, p, m, t);
    for (var g = 0, h = 0, v, _; g < y; ++g)
      if (v = w[g]) {
        for (g >= h && (h = g + 1); !(_ = x[h]) && ++h < y; ) ;
        v._next = _ || null;
      }
  }
  return s = new Qe(s, r), s._enter = l, s._exit = u, s;
}
function Sw(e) {
  return typeof e == "object" && "length" in e ? e : Array.from(e);
}
function _w() {
  return new Qe(this._exit || this._groups.map(Lp), this._parents);
}
function Ew(e, t, n) {
  var r = this.enter(), o = this, i = this.exit();
  return typeof e == "function" ? (r = e(r), r && (r = r.selection())) : r = r.append(e + ""), t != null && (o = t(o), o && (o = o.selection())), n == null ? i.remove() : n(i), r && o ? r.merge(o).order() : o;
}
function kw(e) {
  for (var t = e.selection ? e.selection() : e, n = this._groups, r = t._groups, o = n.length, i = r.length, s = Math.min(o, i), l = new Array(o), u = 0; u < s; ++u)
    for (var a = n[u], d = r[u], c = a.length, f = l[u] = new Array(c), m, y = 0; y < c; ++y)
      (m = a[y] || d[y]) && (f[y] = m);
  for (; u < o; ++u)
    l[u] = n[u];
  return new Qe(l, this._parents);
}
function Nw() {
  for (var e = this._groups, t = -1, n = e.length; ++t < n; )
    for (var r = e[t], o = r.length - 1, i = r[o], s; --o >= 0; )
      (s = r[o]) && (i && s.compareDocumentPosition(i) ^ 4 && i.parentNode.insertBefore(s, i), i = s);
  return this;
}
function Cw(e) {
  e || (e = Mw);
  function t(c, f) {
    return c && f ? e(c.__data__, f.__data__) : !c - !f;
  }
  for (var n = this._groups, r = n.length, o = new Array(r), i = 0; i < r; ++i) {
    for (var s = n[i], l = s.length, u = o[i] = new Array(l), a, d = 0; d < l; ++d)
      (a = s[d]) && (u[d] = a);
    u.sort(t);
  }
  return new Qe(o, this._parents).order();
}
function Mw(e, t) {
  return e < t ? -1 : e > t ? 1 : e >= t ? 0 : NaN;
}
function Pw() {
  var e = arguments[0];
  return arguments[0] = this, e.apply(null, arguments), this;
}
function Iw() {
  return Array.from(this);
}
function zw() {
  for (var e = this._groups, t = 0, n = e.length; t < n; ++t)
    for (var r = e[t], o = 0, i = r.length; o < i; ++o) {
      var s = r[o];
      if (s) return s;
    }
  return null;
}
function Tw() {
  let e = 0;
  for (const t of this) ++e;
  return e;
}
function Lw() {
  return !this.node();
}
function Aw(e) {
  for (var t = this._groups, n = 0, r = t.length; n < r; ++n)
    for (var o = t[n], i = 0, s = o.length, l; i < s; ++i)
      (l = o[i]) && e.call(l, l.__data__, i, o);
  return this;
}
function $w(e) {
  return function() {
    this.removeAttribute(e);
  };
}
function Dw(e) {
  return function() {
    this.removeAttributeNS(e.space, e.local);
  };
}
function Rw(e, t) {
  return function() {
    this.setAttribute(e, t);
  };
}
function jw(e, t) {
  return function() {
    this.setAttributeNS(e.space, e.local, t);
  };
}
function Ow(e, t) {
  return function() {
    var n = t.apply(this, arguments);
    n == null ? this.removeAttribute(e) : this.setAttribute(e, n);
  };
}
function Fw(e, t) {
  return function() {
    var n = t.apply(this, arguments);
    n == null ? this.removeAttributeNS(e.space, e.local) : this.setAttributeNS(e.space, e.local, n);
  };
}
function Hw(e, t) {
  var n = Is(e);
  if (arguments.length < 2) {
    var r = this.node();
    return n.local ? r.getAttributeNS(n.space, n.local) : r.getAttribute(n);
  }
  return this.each((t == null ? n.local ? Dw : $w : typeof t == "function" ? n.local ? Fw : Ow : n.local ? jw : Rw)(n, t));
}
function Ap(e) {
  return e.ownerDocument && e.ownerDocument.defaultView || e.document && e || e.defaultView;
}
function Vw(e) {
  return function() {
    this.style.removeProperty(e);
  };
}
function Bw(e, t, n) {
  return function() {
    this.style.setProperty(e, t, n);
  };
}
function Ww(e, t, n) {
  return function() {
    var r = t.apply(this, arguments);
    r == null ? this.style.removeProperty(e) : this.style.setProperty(e, r, n);
  };
}
function Uw(e, t, n) {
  return arguments.length > 1 ? this.each((t == null ? Vw : typeof t == "function" ? Ww : Bw)(e, t, n ?? "")) : dr(this.node(), e);
}
function dr(e, t) {
  return e.style.getPropertyValue(t) || Ap(e).getComputedStyle(e, null).getPropertyValue(t);
}
function Yw(e) {
  return function() {
    delete this[e];
  };
}
function Xw(e, t) {
  return function() {
    this[e] = t;
  };
}
function bw(e, t) {
  return function() {
    var n = t.apply(this, arguments);
    n == null ? delete this[e] : this[e] = n;
  };
}
function Qw(e, t) {
  return arguments.length > 1 ? this.each((t == null ? Yw : typeof t == "function" ? bw : Xw)(e, t)) : this.node()[e];
}
function $p(e) {
  return e.trim().split(/^|\s+/);
}
function za(e) {
  return e.classList || new Dp(e);
}
function Dp(e) {
  this._node = e, this._names = $p(e.getAttribute("class") || "");
}
Dp.prototype = {
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
function Rp(e, t) {
  for (var n = za(e), r = -1, o = t.length; ++r < o; ) n.add(t[r]);
}
function jp(e, t) {
  for (var n = za(e), r = -1, o = t.length; ++r < o; ) n.remove(t[r]);
}
function Kw(e) {
  return function() {
    Rp(this, e);
  };
}
function Gw(e) {
  return function() {
    jp(this, e);
  };
}
function Zw(e, t) {
  return function() {
    (t.apply(this, arguments) ? Rp : jp)(this, e);
  };
}
function qw(e, t) {
  var n = $p(e + "");
  if (arguments.length < 2) {
    for (var r = za(this.node()), o = -1, i = n.length; ++o < i; ) if (!r.contains(n[o])) return !1;
    return !0;
  }
  return this.each((typeof t == "function" ? Zw : t ? Kw : Gw)(n, t));
}
function Jw() {
  this.textContent = "";
}
function ex(e) {
  return function() {
    this.textContent = e;
  };
}
function tx(e) {
  return function() {
    var t = e.apply(this, arguments);
    this.textContent = t ?? "";
  };
}
function nx(e) {
  return arguments.length ? this.each(e == null ? Jw : (typeof e == "function" ? tx : ex)(e)) : this.node().textContent;
}
function rx() {
  this.innerHTML = "";
}
function ox(e) {
  return function() {
    this.innerHTML = e;
  };
}
function ix(e) {
  return function() {
    var t = e.apply(this, arguments);
    this.innerHTML = t ?? "";
  };
}
function sx(e) {
  return arguments.length ? this.each(e == null ? rx : (typeof e == "function" ? ix : ox)(e)) : this.node().innerHTML;
}
function lx() {
  this.nextSibling && this.parentNode.appendChild(this);
}
function ux() {
  return this.each(lx);
}
function ax() {
  this.previousSibling && this.parentNode.insertBefore(this, this.parentNode.firstChild);
}
function cx() {
  return this.each(ax);
}
function fx(e) {
  var t = typeof e == "function" ? e : Pp(e);
  return this.select(function() {
    return this.appendChild(t.apply(this, arguments));
  });
}
function dx() {
  return null;
}
function hx(e, t) {
  var n = typeof e == "function" ? e : Pp(e), r = t == null ? dx : typeof t == "function" ? t : Ia(t);
  return this.select(function() {
    return this.insertBefore(n.apply(this, arguments), r.apply(this, arguments) || null);
  });
}
function px() {
  var e = this.parentNode;
  e && e.removeChild(this);
}
function gx() {
  return this.each(px);
}
function mx() {
  var e = this.cloneNode(!1), t = this.parentNode;
  return t ? t.insertBefore(e, this.nextSibling) : e;
}
function yx() {
  var e = this.cloneNode(!0), t = this.parentNode;
  return t ? t.insertBefore(e, this.nextSibling) : e;
}
function vx(e) {
  return this.select(e ? yx : mx);
}
function wx(e) {
  return arguments.length ? this.property("__data__", e) : this.node().__data__;
}
function xx(e) {
  return function(t) {
    e.call(this, t, this.__data__);
  };
}
function Sx(e) {
  return e.trim().split(/^|\s+/).map(function(t) {
    var n = "", r = t.indexOf(".");
    return r >= 0 && (n = t.slice(r + 1), t = t.slice(0, r)), { type: t, name: n };
  });
}
function _x(e) {
  return function() {
    var t = this.__on;
    if (t) {
      for (var n = 0, r = -1, o = t.length, i; n < o; ++n)
        i = t[n], (!e.type || i.type === e.type) && i.name === e.name ? this.removeEventListener(i.type, i.listener, i.options) : t[++r] = i;
      ++r ? t.length = r : delete this.__on;
    }
  };
}
function Ex(e, t, n) {
  return function() {
    var r = this.__on, o, i = xx(t);
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
function kx(e, t, n) {
  var r = Sx(e + ""), o, i = r.length, s;
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
  for (l = t ? Ex : _x, o = 0; o < i; ++o) this.each(l(r[o], t, n));
  return this;
}
function Op(e, t, n) {
  var r = Ap(e), o = r.CustomEvent;
  typeof o == "function" ? o = new o(t, n) : (o = r.document.createEvent("Event"), n ? (o.initEvent(t, n.bubbles, n.cancelable), o.detail = n.detail) : o.initEvent(t, !1, !1)), e.dispatchEvent(o);
}
function Nx(e, t) {
  return function() {
    return Op(this, e, t);
  };
}
function Cx(e, t) {
  return function() {
    return Op(this, e, t.apply(this, arguments));
  };
}
function Mx(e, t) {
  return this.each((typeof t == "function" ? Cx : Nx)(e, t));
}
function* Px() {
  for (var e = this._groups, t = 0, n = e.length; t < n; ++t)
    for (var r = e[t], o = 0, i = r.length, s; o < i; ++o)
      (s = r[o]) && (yield s);
}
var Fp = [null];
function Qe(e, t) {
  this._groups = e, this._parents = t;
}
function $o() {
  return new Qe([[document.documentElement]], Fp);
}
function Ix() {
  return this;
}
Qe.prototype = $o.prototype = {
  constructor: Qe,
  select: tw,
  selectAll: iw,
  selectChild: aw,
  selectChildren: hw,
  filter: pw,
  data: xw,
  enter: gw,
  exit: _w,
  join: Ew,
  merge: kw,
  selection: Ix,
  order: Nw,
  sort: Cw,
  call: Pw,
  nodes: Iw,
  node: zw,
  size: Tw,
  empty: Lw,
  each: Aw,
  attr: Hw,
  style: Uw,
  property: Qw,
  classed: qw,
  text: nx,
  html: sx,
  raise: ux,
  lower: cx,
  append: fx,
  insert: hx,
  remove: gx,
  clone: vx,
  datum: wx,
  on: kx,
  dispatch: Mx,
  [Symbol.iterator]: Px
};
function Ue(e) {
  return typeof e == "string" ? new Qe([[document.querySelector(e)]], [document.documentElement]) : new Qe([[e]], Fp);
}
function zx(e) {
  let t;
  for (; t = e.sourceEvent; ) e = t;
  return e;
}
function st(e, t) {
  if (e = zx(e), t === void 0 && (t = e.currentTarget), t) {
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
const Tx = { passive: !1 }, wo = { capture: !0, passive: !1 };
function yl(e) {
  e.stopImmediatePropagation();
}
function rr(e) {
  e.preventDefault(), e.stopImmediatePropagation();
}
function Hp(e) {
  var t = e.document.documentElement, n = Ue(e).on("dragstart.drag", rr, wo);
  "onselectstart" in t ? n.on("selectstart.drag", rr, wo) : (t.__noselect = t.style.MozUserSelect, t.style.MozUserSelect = "none");
}
function Vp(e, t) {
  var n = e.document.documentElement, r = Ue(e).on("dragstart.drag", null);
  t && (r.on("click.drag", rr, wo), setTimeout(function() {
    r.on("click.drag", null);
  }, 0)), "onselectstart" in n ? r.on("selectstart.drag", null) : (n.style.MozUserSelect = n.__noselect, delete n.__noselect);
}
const ii = (e) => () => e;
function xu(e, {
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
xu.prototype.on = function() {
  var e = this._.on.apply(this._, arguments);
  return e === this._ ? this : e;
};
function Lx(e) {
  return !e.ctrlKey && !e.button;
}
function Ax() {
  return this.parentNode;
}
function $x(e, t) {
  return t ?? { x: e.x, y: e.y };
}
function Dx() {
  return navigator.maxTouchPoints || "ontouchstart" in this;
}
function Bp() {
  var e = Lx, t = Ax, n = $x, r = Dx, o = {}, i = Ps("start", "drag", "end"), s = 0, l, u, a, d, c = 0;
  function f(v) {
    v.on("mousedown.drag", m).filter(r).on("touchstart.drag", x).on("touchmove.drag", p, Tx).on("touchend.drag touchcancel.drag", g).style("touch-action", "none").style("-webkit-tap-highlight-color", "rgba(0,0,0,0)");
  }
  function m(v, _) {
    if (!(d || !e.call(this, v, _))) {
      var E = h(this, t.call(this, v, _), v, _, "mouse");
      E && (Ue(v.view).on("mousemove.drag", y, wo).on("mouseup.drag", w, wo), Hp(v.view), yl(v), a = !1, l = v.clientX, u = v.clientY, E("start", v));
    }
  }
  function y(v) {
    if (rr(v), !a) {
      var _ = v.clientX - l, E = v.clientY - u;
      a = _ * _ + E * E > c;
    }
    o.mouse("drag", v);
  }
  function w(v) {
    Ue(v.view).on("mousemove.drag mouseup.drag", null), Vp(v.view, a), rr(v), o.mouse("end", v);
  }
  function x(v, _) {
    if (e.call(this, v, _)) {
      var E = v.changedTouches, M = t.call(this, v, _), P = E.length, T, O;
      for (T = 0; T < P; ++T)
        (O = h(this, M, v, _, E[T].identifier, E[T])) && (yl(v), O("start", v, E[T]));
    }
  }
  function p(v) {
    var _ = v.changedTouches, E = _.length, M, P;
    for (M = 0; M < E; ++M)
      (P = o[_[M].identifier]) && (rr(v), P("drag", v, _[M]));
  }
  function g(v) {
    var _ = v.changedTouches, E = _.length, M, P;
    for (d && clearTimeout(d), d = setTimeout(function() {
      d = null;
    }, 500), M = 0; M < E; ++M)
      (P = o[_[M].identifier]) && (yl(v), P("end", v, _[M]));
  }
  function h(v, _, E, M, P, T) {
    var O = i.copy(), z = st(T || E, _), D, H, k;
    if ((k = n.call(v, new xu("beforestart", {
      sourceEvent: E,
      target: f,
      identifier: P,
      active: s,
      x: z[0],
      y: z[1],
      dx: 0,
      dy: 0,
      dispatch: O
    }), M)) != null)
      return D = k.x - z[0] || 0, H = k.y - z[1] || 0, function A(I, $, N) {
        var C = z, L;
        switch (I) {
          case "start":
            o[P] = A, L = s++;
            break;
          case "end":
            delete o[P], --s;
          case "drag":
            z = st(N || $, _), L = s;
            break;
        }
        O.call(
          I,
          v,
          new xu(I, {
            sourceEvent: $,
            subject: k,
            target: f,
            identifier: P,
            active: L,
            x: z[0] + D,
            y: z[1] + H,
            dx: z[0] - C[0],
            dy: z[1] - C[1],
            dispatch: O
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
function Ta(e, t, n) {
  e.prototype = t.prototype = n, n.constructor = e;
}
function Wp(e, t) {
  var n = Object.create(e.prototype);
  for (var r in t) n[r] = t[r];
  return n;
}
function Do() {
}
var xo = 0.7, ns = 1 / xo, or = "\\s*([+-]?\\d+)\\s*", So = "\\s*([+-]?(?:\\d*\\.)?\\d+(?:[eE][+-]?\\d+)?)\\s*", St = "\\s*([+-]?(?:\\d*\\.)?\\d+(?:[eE][+-]?\\d+)?)%\\s*", Rx = /^#([0-9a-f]{3,8})$/, jx = new RegExp(`^rgb\\(${or},${or},${or}\\)$`), Ox = new RegExp(`^rgb\\(${St},${St},${St}\\)$`), Fx = new RegExp(`^rgba\\(${or},${or},${or},${So}\\)$`), Hx = new RegExp(`^rgba\\(${St},${St},${St},${So}\\)$`), Vx = new RegExp(`^hsl\\(${So},${St},${St}\\)$`), Bx = new RegExp(`^hsla\\(${So},${St},${St},${So}\\)$`), hf = {
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
Ta(Do, Pn, {
  copy(e) {
    return Object.assign(new this.constructor(), this, e);
  },
  displayable() {
    return this.rgb().displayable();
  },
  hex: pf,
  // Deprecated! Use color.formatHex.
  formatHex: pf,
  formatHex8: Wx,
  formatHsl: Ux,
  formatRgb: gf,
  toString: gf
});
function pf() {
  return this.rgb().formatHex();
}
function Wx() {
  return this.rgb().formatHex8();
}
function Ux() {
  return Up(this).formatHsl();
}
function gf() {
  return this.rgb().formatRgb();
}
function Pn(e) {
  var t, n;
  return e = (e + "").trim().toLowerCase(), (t = Rx.exec(e)) ? (n = t[1].length, t = parseInt(t[1], 16), n === 6 ? mf(t) : n === 3 ? new Fe(t >> 8 & 15 | t >> 4 & 240, t >> 4 & 15 | t & 240, (t & 15) << 4 | t & 15, 1) : n === 8 ? si(t >> 24 & 255, t >> 16 & 255, t >> 8 & 255, (t & 255) / 255) : n === 4 ? si(t >> 12 & 15 | t >> 8 & 240, t >> 8 & 15 | t >> 4 & 240, t >> 4 & 15 | t & 240, ((t & 15) << 4 | t & 15) / 255) : null) : (t = jx.exec(e)) ? new Fe(t[1], t[2], t[3], 1) : (t = Ox.exec(e)) ? new Fe(t[1] * 255 / 100, t[2] * 255 / 100, t[3] * 255 / 100, 1) : (t = Fx.exec(e)) ? si(t[1], t[2], t[3], t[4]) : (t = Hx.exec(e)) ? si(t[1] * 255 / 100, t[2] * 255 / 100, t[3] * 255 / 100, t[4]) : (t = Vx.exec(e)) ? wf(t[1], t[2] / 100, t[3] / 100, 1) : (t = Bx.exec(e)) ? wf(t[1], t[2] / 100, t[3] / 100, t[4]) : hf.hasOwnProperty(e) ? mf(hf[e]) : e === "transparent" ? new Fe(NaN, NaN, NaN, 0) : null;
}
function mf(e) {
  return new Fe(e >> 16 & 255, e >> 8 & 255, e & 255, 1);
}
function si(e, t, n, r) {
  return r <= 0 && (e = t = n = NaN), new Fe(e, t, n, r);
}
function Yx(e) {
  return e instanceof Do || (e = Pn(e)), e ? (e = e.rgb(), new Fe(e.r, e.g, e.b, e.opacity)) : new Fe();
}
function Su(e, t, n, r) {
  return arguments.length === 1 ? Yx(e) : new Fe(e, t, n, r ?? 1);
}
function Fe(e, t, n, r) {
  this.r = +e, this.g = +t, this.b = +n, this.opacity = +r;
}
Ta(Fe, Su, Wp(Do, {
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
  hex: yf,
  // Deprecated! Use color.formatHex.
  formatHex: yf,
  formatHex8: Xx,
  formatRgb: vf,
  toString: vf
}));
function yf() {
  return `#${vn(this.r)}${vn(this.g)}${vn(this.b)}`;
}
function Xx() {
  return `#${vn(this.r)}${vn(this.g)}${vn(this.b)}${vn((isNaN(this.opacity) ? 1 : this.opacity) * 255)}`;
}
function vf() {
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
function wf(e, t, n, r) {
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
function bx(e, t, n, r) {
  return arguments.length === 1 ? Up(e) : new ut(e, t, n, r ?? 1);
}
function ut(e, t, n, r) {
  this.h = +e, this.s = +t, this.l = +n, this.opacity = +r;
}
Ta(ut, bx, Wp(Do, {
  brighter(e) {
    return e = e == null ? ns : Math.pow(ns, e), new ut(this.h, this.s, this.l * e, this.opacity);
  },
  darker(e) {
    return e = e == null ? xo : Math.pow(xo, e), new ut(this.h, this.s, this.l * e, this.opacity);
  },
  rgb() {
    var e = this.h % 360 + (this.h < 0) * 360, t = isNaN(e) || isNaN(this.s) ? 0 : this.s, n = this.l, r = n + (n < 0.5 ? n : 1 - n) * t, o = 2 * n - r;
    return new Fe(
      vl(e >= 240 ? e - 240 : e + 120, o, r),
      vl(e, o, r),
      vl(e < 120 ? e + 240 : e - 120, o, r),
      this.opacity
    );
  },
  clamp() {
    return new ut(xf(this.h), li(this.s), li(this.l), rs(this.opacity));
  },
  displayable() {
    return (0 <= this.s && this.s <= 1 || isNaN(this.s)) && 0 <= this.l && this.l <= 1 && 0 <= this.opacity && this.opacity <= 1;
  },
  formatHsl() {
    const e = rs(this.opacity);
    return `${e === 1 ? "hsl(" : "hsla("}${xf(this.h)}, ${li(this.s) * 100}%, ${li(this.l) * 100}%${e === 1 ? ")" : `, ${e})`}`;
  }
}));
function xf(e) {
  return e = (e || 0) % 360, e < 0 ? e + 360 : e;
}
function li(e) {
  return Math.max(0, Math.min(1, e || 0));
}
function vl(e, t, n) {
  return (e < 60 ? t + (n - t) * e / 60 : e < 180 ? n : e < 240 ? t + (n - t) * (240 - e) / 60 : t) * 255;
}
const La = (e) => () => e;
function Qx(e, t) {
  return function(n) {
    return e + n * t;
  };
}
function Kx(e, t, n) {
  return e = Math.pow(e, n), t = Math.pow(t, n) - e, n = 1 / n, function(r) {
    return Math.pow(e + r * t, n);
  };
}
function Gx(e) {
  return (e = +e) == 1 ? Yp : function(t, n) {
    return n - t ? Kx(t, n, e) : La(isNaN(t) ? n : t);
  };
}
function Yp(e, t) {
  var n = t - e;
  return n ? Qx(e, n) : La(isNaN(e) ? t : e);
}
const os = function e(t) {
  var n = Gx(t);
  function r(o, i) {
    var s = n((o = Su(o)).r, (i = Su(i)).r), l = n(o.g, i.g), u = n(o.b, i.b), a = Yp(o.opacity, i.opacity);
    return function(d) {
      return o.r = s(d), o.g = l(d), o.b = u(d), o.opacity = a(d), o + "";
    };
  }
  return r.gamma = e, r;
}(1);
function Zx(e, t) {
  t || (t = []);
  var n = e ? Math.min(t.length, e.length) : 0, r = t.slice(), o;
  return function(i) {
    for (o = 0; o < n; ++o) r[o] = e[o] * (1 - i) + t[o] * i;
    return r;
  };
}
function qx(e) {
  return ArrayBuffer.isView(e) && !(e instanceof DataView);
}
function Jx(e, t) {
  var n = t ? t.length : 0, r = e ? Math.min(n, e.length) : 0, o = new Array(r), i = new Array(n), s;
  for (s = 0; s < r; ++s) o[s] = Jr(e[s], t[s]);
  for (; s < n; ++s) i[s] = t[s];
  return function(l) {
    for (s = 0; s < r; ++s) i[s] = o[s](l);
    return i;
  };
}
function e1(e, t) {
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
function t1(e, t) {
  var n = {}, r = {}, o;
  (e === null || typeof e != "object") && (e = {}), (t === null || typeof t != "object") && (t = {});
  for (o in t)
    o in e ? n[o] = Jr(e[o], t[o]) : r[o] = t[o];
  return function(i) {
    for (o in n) r[o] = n[o](i);
    return r;
  };
}
var _u = /[-+]?(?:\d+\.?\d*|\.?\d+)(?:[eE][-+]?\d+)?/g, wl = new RegExp(_u.source, "g");
function n1(e) {
  return function() {
    return e;
  };
}
function r1(e) {
  return function(t) {
    return e(t) + "";
  };
}
function Xp(e, t) {
  var n = _u.lastIndex = wl.lastIndex = 0, r, o, i, s = -1, l = [], u = [];
  for (e = e + "", t = t + ""; (r = _u.exec(e)) && (o = wl.exec(t)); )
    (i = o.index) > n && (i = t.slice(n, i), l[s] ? l[s] += i : l[++s] = i), (r = r[0]) === (o = o[0]) ? l[s] ? l[s] += o : l[++s] = o : (l[++s] = null, u.push({ i: s, x: yt(r, o) })), n = wl.lastIndex;
  return n < t.length && (i = t.slice(n), l[s] ? l[s] += i : l[++s] = i), l.length < 2 ? u[0] ? r1(u[0].x) : n1(t) : (t = u.length, function(a) {
    for (var d = 0, c; d < t; ++d) l[(c = u[d]).i] = c.x(a);
    return l.join("");
  });
}
function Jr(e, t) {
  var n = typeof t, r;
  return t == null || n === "boolean" ? La(t) : (n === "number" ? yt : n === "string" ? (r = Pn(t)) ? (t = r, os) : Xp : t instanceof Pn ? os : t instanceof Date ? e1 : qx(t) ? Zx : Array.isArray(t) ? Jx : typeof t.valueOf != "function" && typeof t.toString != "function" || isNaN(t) ? t1 : yt)(e, t);
}
var Sf = 180 / Math.PI, Eu = {
  translateX: 0,
  translateY: 0,
  rotate: 0,
  skewX: 0,
  scaleX: 1,
  scaleY: 1
};
function bp(e, t, n, r, o, i) {
  var s, l, u;
  return (s = Math.sqrt(e * e + t * t)) && (e /= s, t /= s), (u = e * n + t * r) && (n -= e * u, r -= t * u), (l = Math.sqrt(n * n + r * r)) && (n /= l, r /= l, u /= l), e * r < t * n && (e = -e, t = -t, u = -u, s = -s), {
    translateX: o,
    translateY: i,
    rotate: Math.atan2(t, e) * Sf,
    skewX: Math.atan(u) * Sf,
    scaleX: s,
    scaleY: l
  };
}
var ui;
function o1(e) {
  const t = new (typeof DOMMatrix == "function" ? DOMMatrix : WebKitCSSMatrix)(e + "");
  return t.isIdentity ? Eu : bp(t.a, t.b, t.c, t.d, t.e, t.f);
}
function i1(e) {
  return e == null || (ui || (ui = document.createElementNS("http://www.w3.org/2000/svg", "g")), ui.setAttribute("transform", e), !(e = ui.transform.baseVal.consolidate())) ? Eu : (e = e.matrix, bp(e.a, e.b, e.c, e.d, e.e, e.f));
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
var s1 = Qp(o1, "px, ", "px)", "deg)"), l1 = Qp(i1, ", ", ")", ")"), u1 = 1e-12;
function _f(e) {
  return ((e = Math.exp(e)) + 1 / e) / 2;
}
function a1(e) {
  return ((e = Math.exp(e)) - 1 / e) / 2;
}
function c1(e) {
  return ((e = Math.exp(2 * e)) - 1) / (e + 1);
}
const Mi = function e(t, n, r) {
  function o(i, s) {
    var l = i[0], u = i[1], a = i[2], d = s[0], c = s[1], f = s[2], m = d - l, y = c - u, w = m * m + y * y, x, p;
    if (w < u1)
      p = Math.log(f / a) / t, x = function(M) {
        return [
          l + M * m,
          u + M * y,
          a * Math.exp(t * M * p)
        ];
      };
    else {
      var g = Math.sqrt(w), h = (f * f - a * a + r * w) / (2 * a * n * g), v = (f * f - a * a - r * w) / (2 * f * n * g), _ = Math.log(Math.sqrt(h * h + 1) - h), E = Math.log(Math.sqrt(v * v + 1) - v);
      p = (E - _) / t, x = function(M) {
        var P = M * p, T = _f(_), O = a / (n * g) * (T * c1(t * P + _) - a1(_));
        return [
          l + O * m,
          u + O * y,
          a * T / _f(t * P + _)
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
var hr = 0, Br = 0, Rr = 0, Kp = 1e3, is, Wr, ss = 0, In = 0, zs = 0, _o = typeof performance == "object" && performance.now ? performance : Date, Gp = typeof window == "object" && window.requestAnimationFrame ? window.requestAnimationFrame.bind(window) : function(e) {
  setTimeout(e, 17);
};
function Aa() {
  return In || (Gp(f1), In = _o.now() + zs);
}
function f1() {
  In = 0;
}
function ls() {
  this._call = this._time = this._next = null;
}
ls.prototype = Zp.prototype = {
  constructor: ls,
  restart: function(e, t, n) {
    if (typeof e != "function") throw new TypeError("callback is not a function");
    n = (n == null ? Aa() : +n) + (t == null ? 0 : +t), !this._next && Wr !== this && (Wr ? Wr._next = this : is = this, Wr = this), this._call = e, this._time = n, ku();
  },
  stop: function() {
    this._call && (this._call = null, this._time = 1 / 0, ku());
  }
};
function Zp(e, t, n) {
  var r = new ls();
  return r.restart(e, t, n), r;
}
function d1() {
  Aa(), ++hr;
  for (var e = is, t; e; )
    (t = In - e._time) >= 0 && e._call.call(void 0, t), e = e._next;
  --hr;
}
function Ef() {
  In = (ss = _o.now()) + zs, hr = Br = 0;
  try {
    d1();
  } finally {
    hr = 0, p1(), In = 0;
  }
}
function h1() {
  var e = _o.now(), t = e - ss;
  t > Kp && (zs -= t, ss = e);
}
function p1() {
  for (var e, t = is, n, r = 1 / 0; t; )
    t._call ? (r > t._time && (r = t._time), e = t, t = t._next) : (n = t._next, t._next = null, t = e ? e._next = n : is = n);
  Wr = e, ku(r);
}
function ku(e) {
  if (!hr) {
    Br && (Br = clearTimeout(Br));
    var t = e - In;
    t > 24 ? (e < 1 / 0 && (Br = setTimeout(Ef, e - _o.now() - zs)), Rr && (Rr = clearInterval(Rr))) : (Rr || (ss = _o.now(), Rr = setInterval(h1, Kp)), hr = 1, Gp(Ef));
  }
}
function kf(e, t, n) {
  var r = new ls();
  return t = t == null ? 0 : +t, r.restart((o) => {
    r.stop(), e(o + t);
  }, t, n), r;
}
var g1 = Ps("start", "end", "cancel", "interrupt"), m1 = [], qp = 0, Nf = 1, Nu = 2, Pi = 3, Cf = 4, Cu = 5, Ii = 6;
function Ts(e, t, n, r, o, i) {
  var s = e.__transition;
  if (!s) e.__transition = {};
  else if (n in s) return;
  y1(e, n, {
    name: t,
    index: r,
    // For context during callback.
    group: o,
    // For context during callback.
    on: g1,
    tween: m1,
    time: i.time,
    delay: i.delay,
    duration: i.duration,
    ease: i.ease,
    timer: null,
    state: qp
  });
}
function $a(e, t) {
  var n = pt(e, t);
  if (n.state > qp) throw new Error("too late; already scheduled");
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
function y1(e, t, n) {
  var r = e.__transition, o;
  r[t] = n, n.timer = Zp(i, 0, n.time);
  function i(a) {
    n.state = Nf, n.timer.restart(s, n.delay, n.time), n.delay <= a && s(a - n.delay);
  }
  function s(a) {
    var d, c, f, m;
    if (n.state !== Nf) return u();
    for (d in r)
      if (m = r[d], m.name === n.name) {
        if (m.state === Pi) return kf(s);
        m.state === Cf ? (m.state = Ii, m.timer.stop(), m.on.call("interrupt", e, e.__data__, m.index, m.group), delete r[d]) : +d < t && (m.state = Ii, m.timer.stop(), m.on.call("cancel", e, e.__data__, m.index, m.group), delete r[d]);
      }
    if (kf(function() {
      n.state === Pi && (n.state = Cf, n.timer.restart(l, n.delay, n.time), l(a));
    }), n.state = Nu, n.on.call("start", e, e.__data__, n.index, n.group), n.state === Nu) {
      for (n.state = Pi, o = new Array(f = n.tween.length), d = 0, c = -1; d < f; ++d)
        (m = n.tween[d].value.call(e, e.__data__, n.index, n.group)) && (o[++c] = m);
      o.length = c + 1;
    }
  }
  function l(a) {
    for (var d = a < n.duration ? n.ease.call(null, a / n.duration) : (n.timer.restart(u), n.state = Cu, 1), c = -1, f = o.length; ++c < f; )
      o[c].call(e, d);
    n.state === Cu && (n.on.call("end", e, e.__data__, n.index, n.group), u());
  }
  function u() {
    n.state = Ii, n.timer.stop(), delete r[t];
    for (var a in r) return;
    delete e.__transition;
  }
}
function zi(e, t) {
  var n = e.__transition, r, o, i = !0, s;
  if (n) {
    t = t == null ? null : t + "";
    for (s in n) {
      if ((r = n[s]).name !== t) {
        i = !1;
        continue;
      }
      o = r.state > Nu && r.state < Cu, r.state = Ii, r.timer.stop(), r.on.call(o ? "interrupt" : "cancel", e, e.__data__, r.index, r.group), delete n[s];
    }
    i && delete e.__transition;
  }
}
function v1(e) {
  return this.each(function() {
    zi(this, e);
  });
}
function w1(e, t) {
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
function x1(e, t, n) {
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
function S1(e, t) {
  var n = this._id;
  if (e += "", arguments.length < 2) {
    for (var r = pt(this.node(), n).tween, o = 0, i = r.length, s; o < i; ++o)
      if ((s = r[o]).name === e)
        return s.value;
    return null;
  }
  return this.each((t == null ? w1 : x1)(n, e, t));
}
function Da(e, t, n) {
  var r = e._id;
  return e.each(function() {
    var o = Et(this, r);
    (o.value || (o.value = {}))[t] = n.apply(this, arguments);
  }), function(o) {
    return pt(o, r).value[t];
  };
}
function Jp(e, t) {
  var n;
  return (typeof t == "number" ? yt : t instanceof Pn ? os : (n = Pn(t)) ? (t = n, os) : Xp)(e, t);
}
function _1(e) {
  return function() {
    this.removeAttribute(e);
  };
}
function E1(e) {
  return function() {
    this.removeAttributeNS(e.space, e.local);
  };
}
function k1(e, t, n) {
  var r, o = n + "", i;
  return function() {
    var s = this.getAttribute(e);
    return s === o ? null : s === r ? i : i = t(r = s, n);
  };
}
function N1(e, t, n) {
  var r, o = n + "", i;
  return function() {
    var s = this.getAttributeNS(e.space, e.local);
    return s === o ? null : s === r ? i : i = t(r = s, n);
  };
}
function C1(e, t, n) {
  var r, o, i;
  return function() {
    var s, l = n(this), u;
    return l == null ? void this.removeAttribute(e) : (s = this.getAttribute(e), u = l + "", s === u ? null : s === r && u === o ? i : (o = u, i = t(r = s, l)));
  };
}
function M1(e, t, n) {
  var r, o, i;
  return function() {
    var s, l = n(this), u;
    return l == null ? void this.removeAttributeNS(e.space, e.local) : (s = this.getAttributeNS(e.space, e.local), u = l + "", s === u ? null : s === r && u === o ? i : (o = u, i = t(r = s, l)));
  };
}
function P1(e, t) {
  var n = Is(e), r = n === "transform" ? l1 : Jp;
  return this.attrTween(e, typeof t == "function" ? (n.local ? M1 : C1)(n, r, Da(this, "attr." + e, t)) : t == null ? (n.local ? E1 : _1)(n) : (n.local ? N1 : k1)(n, r, t));
}
function I1(e, t) {
  return function(n) {
    this.setAttribute(e, t.call(this, n));
  };
}
function z1(e, t) {
  return function(n) {
    this.setAttributeNS(e.space, e.local, t.call(this, n));
  };
}
function T1(e, t) {
  var n, r;
  function o() {
    var i = t.apply(this, arguments);
    return i !== r && (n = (r = i) && z1(e, i)), n;
  }
  return o._value = t, o;
}
function L1(e, t) {
  var n, r;
  function o() {
    var i = t.apply(this, arguments);
    return i !== r && (n = (r = i) && I1(e, i)), n;
  }
  return o._value = t, o;
}
function A1(e, t) {
  var n = "attr." + e;
  if (arguments.length < 2) return (n = this.tween(n)) && n._value;
  if (t == null) return this.tween(n, null);
  if (typeof t != "function") throw new Error();
  var r = Is(e);
  return this.tween(n, (r.local ? T1 : L1)(r, t));
}
function $1(e, t) {
  return function() {
    $a(this, e).delay = +t.apply(this, arguments);
  };
}
function D1(e, t) {
  return t = +t, function() {
    $a(this, e).delay = t;
  };
}
function R1(e) {
  var t = this._id;
  return arguments.length ? this.each((typeof e == "function" ? $1 : D1)(t, e)) : pt(this.node(), t).delay;
}
function j1(e, t) {
  return function() {
    Et(this, e).duration = +t.apply(this, arguments);
  };
}
function O1(e, t) {
  return t = +t, function() {
    Et(this, e).duration = t;
  };
}
function F1(e) {
  var t = this._id;
  return arguments.length ? this.each((typeof e == "function" ? j1 : O1)(t, e)) : pt(this.node(), t).duration;
}
function H1(e, t) {
  if (typeof t != "function") throw new Error();
  return function() {
    Et(this, e).ease = t;
  };
}
function V1(e) {
  var t = this._id;
  return arguments.length ? this.each(H1(t, e)) : pt(this.node(), t).ease;
}
function B1(e, t) {
  return function() {
    var n = t.apply(this, arguments);
    if (typeof n != "function") throw new Error();
    Et(this, e).ease = n;
  };
}
function W1(e) {
  if (typeof e != "function") throw new Error();
  return this.each(B1(this._id, e));
}
function U1(e) {
  typeof e != "function" && (e = zp(e));
  for (var t = this._groups, n = t.length, r = new Array(n), o = 0; o < n; ++o)
    for (var i = t[o], s = i.length, l = r[o] = [], u, a = 0; a < s; ++a)
      (u = i[a]) && e.call(u, u.__data__, a, i) && l.push(u);
  return new Rt(r, this._parents, this._name, this._id);
}
function Y1(e) {
  if (e._id !== this._id) throw new Error();
  for (var t = this._groups, n = e._groups, r = t.length, o = n.length, i = Math.min(r, o), s = new Array(r), l = 0; l < i; ++l)
    for (var u = t[l], a = n[l], d = u.length, c = s[l] = new Array(d), f, m = 0; m < d; ++m)
      (f = u[m] || a[m]) && (c[m] = f);
  for (; l < r; ++l)
    s[l] = t[l];
  return new Rt(s, this._parents, this._name, this._id);
}
function X1(e) {
  return (e + "").trim().split(/^|\s+/).every(function(t) {
    var n = t.indexOf(".");
    return n >= 0 && (t = t.slice(0, n)), !t || t === "start";
  });
}
function b1(e, t, n) {
  var r, o, i = X1(t) ? $a : Et;
  return function() {
    var s = i(this, e), l = s.on;
    l !== r && (o = (r = l).copy()).on(t, n), s.on = o;
  };
}
function Q1(e, t) {
  var n = this._id;
  return arguments.length < 2 ? pt(this.node(), n).on.on(e) : this.each(b1(n, e, t));
}
function K1(e) {
  return function() {
    var t = this.parentNode;
    for (var n in this.__transition) if (+n !== e) return;
    t && t.removeChild(this);
  };
}
function G1() {
  return this.on("end.remove", K1(this._id));
}
function Z1(e) {
  var t = this._name, n = this._id;
  typeof e != "function" && (e = Ia(e));
  for (var r = this._groups, o = r.length, i = new Array(o), s = 0; s < o; ++s)
    for (var l = r[s], u = l.length, a = i[s] = new Array(u), d, c, f = 0; f < u; ++f)
      (d = l[f]) && (c = e.call(d, d.__data__, f, l)) && ("__data__" in d && (c.__data__ = d.__data__), a[f] = c, Ts(a[f], t, n, f, a, pt(d, n)));
  return new Rt(i, this._parents, t, n);
}
function q1(e) {
  var t = this._name, n = this._id;
  typeof e != "function" && (e = Ip(e));
  for (var r = this._groups, o = r.length, i = [], s = [], l = 0; l < o; ++l)
    for (var u = r[l], a = u.length, d, c = 0; c < a; ++c)
      if (d = u[c]) {
        for (var f = e.call(d, d.__data__, c, u), m, y = pt(d, n), w = 0, x = f.length; w < x; ++w)
          (m = f[w]) && Ts(m, t, n, w, f, y);
        i.push(f), s.push(d);
      }
  return new Rt(i, s, t, n);
}
var J1 = $o.prototype.constructor;
function eS() {
  return new J1(this._groups, this._parents);
}
function tS(e, t) {
  var n, r, o;
  return function() {
    var i = dr(this, e), s = (this.style.removeProperty(e), dr(this, e));
    return i === s ? null : i === n && s === r ? o : o = t(n = i, r = s);
  };
}
function eg(e) {
  return function() {
    this.style.removeProperty(e);
  };
}
function nS(e, t, n) {
  var r, o = n + "", i;
  return function() {
    var s = dr(this, e);
    return s === o ? null : s === r ? i : i = t(r = s, n);
  };
}
function rS(e, t, n) {
  var r, o, i;
  return function() {
    var s = dr(this, e), l = n(this), u = l + "";
    return l == null && (u = l = (this.style.removeProperty(e), dr(this, e))), s === u ? null : s === r && u === o ? i : (o = u, i = t(r = s, l));
  };
}
function oS(e, t) {
  var n, r, o, i = "style." + t, s = "end." + i, l;
  return function() {
    var u = Et(this, e), a = u.on, d = u.value[i] == null ? l || (l = eg(t)) : void 0;
    (a !== n || o !== d) && (r = (n = a).copy()).on(s, o = d), u.on = r;
  };
}
function iS(e, t, n) {
  var r = (e += "") == "transform" ? s1 : Jp;
  return t == null ? this.styleTween(e, tS(e, r)).on("end.style." + e, eg(e)) : typeof t == "function" ? this.styleTween(e, rS(e, r, Da(this, "style." + e, t))).each(oS(this._id, e)) : this.styleTween(e, nS(e, r, t), n).on("end.style." + e, null);
}
function sS(e, t, n) {
  return function(r) {
    this.style.setProperty(e, t.call(this, r), n);
  };
}
function lS(e, t, n) {
  var r, o;
  function i() {
    var s = t.apply(this, arguments);
    return s !== o && (r = (o = s) && sS(e, s, n)), r;
  }
  return i._value = t, i;
}
function uS(e, t, n) {
  var r = "style." + (e += "");
  if (arguments.length < 2) return (r = this.tween(r)) && r._value;
  if (t == null) return this.tween(r, null);
  if (typeof t != "function") throw new Error();
  return this.tween(r, lS(e, t, n ?? ""));
}
function aS(e) {
  return function() {
    this.textContent = e;
  };
}
function cS(e) {
  return function() {
    var t = e(this);
    this.textContent = t ?? "";
  };
}
function fS(e) {
  return this.tween("text", typeof e == "function" ? cS(Da(this, "text", e)) : aS(e == null ? "" : e + ""));
}
function dS(e) {
  return function(t) {
    this.textContent = e.call(this, t);
  };
}
function hS(e) {
  var t, n;
  function r() {
    var o = e.apply(this, arguments);
    return o !== n && (t = (n = o) && dS(o)), t;
  }
  return r._value = e, r;
}
function pS(e) {
  var t = "text";
  if (arguments.length < 1) return (t = this.tween(t)) && t._value;
  if (e == null) return this.tween(t, null);
  if (typeof e != "function") throw new Error();
  return this.tween(t, hS(e));
}
function gS() {
  for (var e = this._name, t = this._id, n = tg(), r = this._groups, o = r.length, i = 0; i < o; ++i)
    for (var s = r[i], l = s.length, u, a = 0; a < l; ++a)
      if (u = s[a]) {
        var d = pt(u, t);
        Ts(u, e, n, a, s, {
          time: d.time + d.delay + d.duration,
          delay: 0,
          duration: d.duration,
          ease: d.ease
        });
      }
  return new Rt(r, this._parents, e, n);
}
function mS() {
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
var yS = 0;
function Rt(e, t, n, r) {
  this._groups = e, this._parents = t, this._name = n, this._id = r;
}
function tg() {
  return ++yS;
}
var Nt = $o.prototype;
Rt.prototype = {
  constructor: Rt,
  select: Z1,
  selectAll: q1,
  selectChild: Nt.selectChild,
  selectChildren: Nt.selectChildren,
  filter: U1,
  merge: Y1,
  selection: eS,
  transition: gS,
  call: Nt.call,
  nodes: Nt.nodes,
  node: Nt.node,
  size: Nt.size,
  empty: Nt.empty,
  each: Nt.each,
  on: Q1,
  attr: P1,
  attrTween: A1,
  style: iS,
  styleTween: uS,
  text: fS,
  textTween: pS,
  remove: G1,
  tween: S1,
  delay: R1,
  duration: F1,
  ease: V1,
  easeVarying: W1,
  end: mS,
  [Symbol.iterator]: Nt[Symbol.iterator]
};
function vS(e) {
  return ((e *= 2) <= 1 ? e * e * e : (e -= 2) * e * e + 2) / 2;
}
var wS = {
  time: null,
  // Set on use.
  delay: 0,
  duration: 250,
  ease: vS
};
function xS(e, t) {
  for (var n; !(n = e.__transition) || !(n = n[t]); )
    if (!(e = e.parentNode))
      throw new Error(`transition ${t} not found`);
  return n;
}
function SS(e) {
  var t, n;
  e instanceof Rt ? (t = e._id, e = e._name) : (t = tg(), (n = wS).time = Aa(), e = e == null ? null : e + "");
  for (var r = this._groups, o = r.length, i = 0; i < o; ++i)
    for (var s = r[i], l = s.length, u, a = 0; a < l; ++a)
      (u = s[a]) && Ts(u, e, t, a, s, n || xS(u, t));
  return new Rt(r, this._parents, e, t);
}
$o.prototype.interrupt = v1;
$o.prototype.transition = SS;
const ai = (e) => () => e;
function _S(e, {
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
function zt(e, t, n) {
  this.k = e, this.x = t, this.y = n;
}
zt.prototype = {
  constructor: zt,
  scale: function(e) {
    return e === 1 ? this : new zt(this.k * e, this.x, this.y);
  },
  translate: function(e, t) {
    return e === 0 & t === 0 ? this : new zt(this.k, this.x + this.k * e, this.y + this.k * t);
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
var Ls = new zt(1, 0, 0);
ng.prototype = zt.prototype;
function ng(e) {
  for (; !e.__zoom; ) if (!(e = e.parentNode)) return Ls;
  return e.__zoom;
}
function xl(e) {
  e.stopImmediatePropagation();
}
function jr(e) {
  e.preventDefault(), e.stopImmediatePropagation();
}
function ES(e) {
  return (!e.ctrlKey || e.type === "wheel") && !e.button;
}
function kS() {
  var e = this;
  return e instanceof SVGElement ? (e = e.ownerSVGElement || e, e.hasAttribute("viewBox") ? (e = e.viewBox.baseVal, [[e.x, e.y], [e.x + e.width, e.y + e.height]]) : [[0, 0], [e.width.baseVal.value, e.height.baseVal.value]]) : [[0, 0], [e.clientWidth, e.clientHeight]];
}
function Mf() {
  return this.__zoom || Ls;
}
function NS(e) {
  return -e.deltaY * (e.deltaMode === 1 ? 0.05 : e.deltaMode ? 1 : 2e-3) * (e.ctrlKey ? 10 : 1);
}
function CS() {
  return navigator.maxTouchPoints || "ontouchstart" in this;
}
function MS(e, t, n) {
  var r = e.invertX(t[0][0]) - n[0][0], o = e.invertX(t[1][0]) - n[1][0], i = e.invertY(t[0][1]) - n[0][1], s = e.invertY(t[1][1]) - n[1][1];
  return e.translate(
    o > r ? (r + o) / 2 : Math.min(0, r) || Math.max(0, o),
    s > i ? (i + s) / 2 : Math.min(0, i) || Math.max(0, s)
  );
}
function rg() {
  var e = ES, t = kS, n = MS, r = NS, o = CS, i = [0, 1 / 0], s = [[-1 / 0, -1 / 0], [1 / 0, 1 / 0]], l = 250, u = Mi, a = Ps("start", "zoom", "end"), d, c, f, m = 500, y = 150, w = 0, x = 10;
  function p(k) {
    k.property("__zoom", Mf).on("wheel.zoom", P, { passive: !1 }).on("mousedown.zoom", T).on("dblclick.zoom", O).filter(o).on("touchstart.zoom", z).on("touchmove.zoom", D).on("touchend.zoom touchcancel.zoom", H).style("-webkit-tap-highlight-color", "rgba(0,0,0,0)");
  }
  p.transform = function(k, A, I, $) {
    var N = k.selection ? k.selection() : k;
    N.property("__zoom", Mf), k !== N ? _(k, A, I, $) : N.interrupt().each(function() {
      E(this, arguments).event($).start().zoom(null, typeof A == "function" ? A.apply(this, arguments) : A).end();
    });
  }, p.scaleBy = function(k, A, I, $) {
    p.scaleTo(k, function() {
      var N = this.__zoom.k, C = typeof A == "function" ? A.apply(this, arguments) : A;
      return N * C;
    }, I, $);
  }, p.scaleTo = function(k, A, I, $) {
    p.transform(k, function() {
      var N = t.apply(this, arguments), C = this.__zoom, L = I == null ? v(N) : typeof I == "function" ? I.apply(this, arguments) : I, F = C.invert(L), R = typeof A == "function" ? A.apply(this, arguments) : A;
      return n(h(g(C, R), L, F), N, s);
    }, I, $);
  }, p.translateBy = function(k, A, I, $) {
    p.transform(k, function() {
      return n(this.__zoom.translate(
        typeof A == "function" ? A.apply(this, arguments) : A,
        typeof I == "function" ? I.apply(this, arguments) : I
      ), t.apply(this, arguments), s);
    }, null, $);
  }, p.translateTo = function(k, A, I, $, N) {
    p.transform(k, function() {
      var C = t.apply(this, arguments), L = this.__zoom, F = $ == null ? v(C) : typeof $ == "function" ? $.apply(this, arguments) : $;
      return n(Ls.translate(F[0], F[1]).scale(L.k).translate(
        typeof A == "function" ? -A.apply(this, arguments) : -A,
        typeof I == "function" ? -I.apply(this, arguments) : -I
      ), C, s);
    }, $, N);
  };
  function g(k, A) {
    return A = Math.max(i[0], Math.min(i[1], A)), A === k.k ? k : new zt(A, k.x, k.y);
  }
  function h(k, A, I) {
    var $ = A[0] - I[0] * k.k, N = A[1] - I[1] * k.k;
    return $ === k.x && N === k.y ? k : new zt(k.k, $, N);
  }
  function v(k) {
    return [(+k[0][0] + +k[1][0]) / 2, (+k[0][1] + +k[1][1]) / 2];
  }
  function _(k, A, I, $) {
    k.on("start.zoom", function() {
      E(this, arguments).event($).start();
    }).on("interrupt.zoom end.zoom", function() {
      E(this, arguments).event($).end();
    }).tween("zoom", function() {
      var N = this, C = arguments, L = E(N, C).event($), F = t.apply(N, C), R = I == null ? v(F) : typeof I == "function" ? I.apply(N, C) : I, U = Math.max(F[1][0] - F[0][0], F[1][1] - F[0][1]), W = N.__zoom, X = typeof A == "function" ? A.apply(N, C) : A, b = u(W.invert(R).concat(U / W.k), X.invert(R).concat(U / X.k));
      return function(K) {
        if (K === 1) K = X;
        else {
          var B = b(K), G = U / B[2];
          K = new zt(G, R[0] - B[0] * G, R[1] - B[1] * G);
        }
        L.zoom(null, K);
      };
    });
  }
  function E(k, A, I) {
    return !I && k.__zooming || new M(k, A);
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
      var A = Ue(this.that).datum();
      a.call(
        k,
        this.that,
        new _S(k, {
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
    var I = E(this, A).event(k), $ = this.__zoom, N = Math.max(i[0], Math.min(i[1], $.k * Math.pow(2, r.apply(this, arguments)))), C = st(k);
    if (I.wheel)
      (I.mouse[0][0] !== C[0] || I.mouse[0][1] !== C[1]) && (I.mouse[1] = $.invert(I.mouse[0] = C)), clearTimeout(I.wheel);
    else {
      if ($.k === N) return;
      I.mouse = [C, $.invert(C)], zi(this), I.start();
    }
    jr(k), I.wheel = setTimeout(L, y), I.zoom("mouse", n(h(g($, N), I.mouse[0], I.mouse[1]), I.extent, s));
    function L() {
      I.wheel = null, I.end();
    }
  }
  function T(k, ...A) {
    if (f || !e.apply(this, arguments)) return;
    var I = k.currentTarget, $ = E(this, A, !0).event(k), N = Ue(k.view).on("mousemove.zoom", R, !0).on("mouseup.zoom", U, !0), C = st(k, I), L = k.clientX, F = k.clientY;
    Hp(k.view), xl(k), $.mouse = [C, this.__zoom.invert(C)], zi(this), $.start();
    function R(W) {
      if (jr(W), !$.moved) {
        var X = W.clientX - L, b = W.clientY - F;
        $.moved = X * X + b * b > w;
      }
      $.event(W).zoom("mouse", n(h($.that.__zoom, $.mouse[0] = st(W, I), $.mouse[1]), $.extent, s));
    }
    function U(W) {
      N.on("mousemove.zoom mouseup.zoom", null), Vp(W.view, $.moved), jr(W), $.event(W).end();
    }
  }
  function O(k, ...A) {
    if (e.apply(this, arguments)) {
      var I = this.__zoom, $ = st(k.changedTouches ? k.changedTouches[0] : k, this), N = I.invert($), C = I.k * (k.shiftKey ? 0.5 : 2), L = n(h(g(I, C), $, N), t.apply(this, A), s);
      jr(k), l > 0 ? Ue(this).transition().duration(l).call(_, L, $, k) : Ue(this).call(p.transform, L, $, k);
    }
  }
  function z(k, ...A) {
    if (e.apply(this, arguments)) {
      var I = k.touches, $ = I.length, N = E(this, A, k.changedTouches.length === $).event(k), C, L, F, R;
      for (xl(k), L = 0; L < $; ++L)
        F = I[L], R = st(F, this), R = [R, this.__zoom.invert(R), F.identifier], N.touch0 ? !N.touch1 && N.touch0[2] !== R[2] && (N.touch1 = R, N.taps = 0) : (N.touch0 = R, C = !0, N.taps = 1 + !!d);
      d && (d = clearTimeout(d)), C && (N.taps < 2 && (c = R[0], d = setTimeout(function() {
        d = null;
      }, m)), zi(this), N.start());
    }
  }
  function D(k, ...A) {
    if (this.__zooming) {
      var I = E(this, A).event(k), $ = k.changedTouches, N = $.length, C, L, F, R;
      for (jr(k), C = 0; C < N; ++C)
        L = $[C], F = st(L, this), I.touch0 && I.touch0[2] === L.identifier ? I.touch0[0] = F : I.touch1 && I.touch1[2] === L.identifier && (I.touch1[0] = F);
      if (L = I.that.__zoom, I.touch1) {
        var U = I.touch0[0], W = I.touch0[1], X = I.touch1[0], b = I.touch1[1], K = (K = X[0] - U[0]) * K + (K = X[1] - U[1]) * K, B = (B = b[0] - W[0]) * B + (B = b[1] - W[1]) * B;
        L = g(L, Math.sqrt(K / B)), F = [(U[0] + X[0]) / 2, (U[1] + X[1]) / 2], R = [(W[0] + b[0]) / 2, (W[1] + b[1]) / 2];
      } else if (I.touch0) F = I.touch0[0], R = I.touch0[1];
      else return;
      I.zoom("touch", n(h(L, F, R), I.extent, s));
    }
  }
  function H(k, ...A) {
    if (this.__zooming) {
      var I = E(this, A).event(k), $ = k.changedTouches, N = $.length, C, L;
      for (xl(k), f && clearTimeout(f), f = setTimeout(function() {
        f = null;
      }, m), C = 0; C < N; ++C)
        L = $[C], I.touch0 && I.touch0[2] === L.identifier ? delete I.touch0 : I.touch1 && I.touch1[2] === L.identifier && delete I.touch1;
      if (I.touch1 && !I.touch0 && (I.touch0 = I.touch1, delete I.touch1), I.touch0) I.touch0[1] = this.__zoom.invert(I.touch0[0]);
      else if (I.end(), I.taps === 2 && (L = st(L, this), Math.hypot(c[0] - L[0], c[1] - L[1]) < x)) {
        var F = Ue(this).on("dblclick.zoom");
        F && F.apply(this, arguments);
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
], og = ["Enter", " ", "Escape"], ig = {
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
const sg = {
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
var bt;
(function(e) {
  e.Bezier = "default", e.Straight = "straight", e.Step = "step", e.SmoothStep = "smoothstep", e.SimpleBezier = "simplebezier";
})(bt || (bt = {}));
var No;
(function(e) {
  e.Arrow = "arrow", e.ArrowClosed = "arrowclosed";
})(No || (No = {}));
var Q;
(function(e) {
  e.Left = "left", e.Top = "top", e.Right = "right", e.Bottom = "bottom";
})(Q || (Q = {}));
const Pf = {
  [Q.Left]: Q.Right,
  [Q.Right]: Q.Left,
  [Q.Top]: Q.Bottom,
  [Q.Bottom]: Q.Top
};
function lg(e) {
  return e === null ? null : e ? "valid" : "invalid";
}
const ug = (e) => "id" in e && "source" in e && "target" in e, PS = (e) => "id" in e && "position" in e && !("source" in e) && !("target" in e), Ra = (e) => "id" in e && "internals" in e && !("source" in e) && !("target" in e), Ro = (e, t = [0, 0]) => {
  const { width: n, height: r } = Ot(e), o = e.origin ?? t, i = n * o[0], s = r * o[1];
  return {
    x: e.position.x - i,
    y: e.position.y - s
  };
}, IS = (e, t = { nodeOrigin: [0, 0] }) => {
  if (e.length === 0)
    return { x: 0, y: 0, width: 0, height: 0 };
  const n = e.reduce((r, o) => {
    const i = typeof o == "string";
    let s = !t.nodeLookup && !i ? o : void 0;
    t.nodeLookup && (s = i ? t.nodeLookup.get(o) : Ra(o) ? o : t.nodeLookup.get(o.id));
    const l = s ? us(s, t.nodeOrigin) : { x: 0, y: 0, x2: 0, y2: 0 };
    return As(r, l);
  }, { x: 1 / 0, y: 1 / 0, x2: -1 / 0, y2: -1 / 0 });
  return $s(n);
}, jo = (e, t = {}) => {
  let n = { x: 1 / 0, y: 1 / 0, x2: -1 / 0, y2: -1 / 0 }, r = !1;
  return e.forEach((o) => {
    (t.filter === void 0 || t.filter(o)) && (n = As(n, us(o)), r = !0);
  }), r ? $s(n) : { x: 0, y: 0, width: 0, height: 0 };
}, ja = (e, t, [n, r, o] = [0, 0, 1], i = !1, s = !1) => {
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
}, zS = (e, t) => {
  const n = /* @__PURE__ */ new Set();
  return e.forEach((r) => {
    n.add(r.id);
  }), t.filter((r) => n.has(r.source) || n.has(r.target));
};
function TS(e, t) {
  const n = /* @__PURE__ */ new Map(), r = t != null && t.nodes ? new Set(t.nodes.map((o) => o.id)) : null;
  return e.forEach((o) => {
    o.measured.width && o.measured.height && ((t == null ? void 0 : t.includeHiddenNodes) || !o.hidden) && (!r || r.has(o.id)) && n.set(o.id, o);
  }), n;
}
async function LS({ nodes: e, width: t, height: n, panZoom: r, minZoom: o, maxZoom: i }, s) {
  if (e.size === 0)
    return Promise.resolve(!0);
  const l = TS(e, s), u = jo(l), a = Oa(u, t, n, (s == null ? void 0 : s.minZoom) ?? o, (s == null ? void 0 : s.maxZoom) ?? i, (s == null ? void 0 : s.padding) ?? 0.1);
  return await r.setViewport(a, {
    duration: s == null ? void 0 : s.duration,
    ease: s == null ? void 0 : s.ease,
    interpolate: s == null ? void 0 : s.interpolate
  }), Promise.resolve(!0);
}
function ag({ nodeId: e, nextPosition: t, nodeLookup: n, nodeOrigin: r = [0, 0], nodeExtent: o, onError: i }) {
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
  const f = yr(c) ? zn(t, c, s.measured) : t;
  return (s.measured.width === void 0 || s.measured.height === void 0) && (i == null || i("015", _t.error015())), {
    position: {
      x: f.x - u + (s.measured.width ?? 0) * d[0],
      y: f.y - a + (s.measured.height ?? 0) * d[1]
    },
    positionAbsolute: f
  };
}
async function AS({ nodesToRemove: e = [], edgesToRemove: t = [], nodes: n, edges: r, onBeforeDelete: o }) {
  const i = new Set(e.map((f) => f.id)), s = [];
  for (const f of n) {
    if (f.deletable === !1)
      continue;
    const m = i.has(f.id), y = !m && f.parentId && s.find((w) => w.id === f.parentId);
    (m || y) && s.push(f);
  }
  const l = new Set(t.map((f) => f.id)), u = r.filter((f) => f.deletable !== !1), d = zS(s, u);
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
const gr = (e, t = 0, n = 1) => Math.min(Math.max(e, t), n), zn = (e = { x: 0, y: 0 }, t, n) => ({
  x: gr(e.x, t[0][0], t[1][0] - ((n == null ? void 0 : n.width) ?? 0)),
  y: gr(e.y, t[0][1], t[1][1] - ((n == null ? void 0 : n.height) ?? 0))
});
function cg(e, t, n) {
  const { width: r, height: o } = Ot(n), { x: i, y: s } = n.internals.positionAbsolute;
  return zn(e, [
    [i, s],
    [i + r, s + o]
  ], t);
}
const If = (e, t, n) => e < t ? gr(Math.abs(e - t), 1, t) / t : e > n ? -gr(Math.abs(e - n), 1, t) / t : 0, fg = (e, t, n = 15, r = 40) => {
  const o = If(e.x, r, t.width - r) * n, i = If(e.y, r, t.height - r) * n;
  return [o, i];
}, As = (e, t) => ({
  x: Math.min(e.x, t.x),
  y: Math.min(e.y, t.y),
  x2: Math.max(e.x2, t.x2),
  y2: Math.max(e.y2, t.y2)
}), Mu = ({ x: e, y: t, width: n, height: r }) => ({
  x: e,
  y: t,
  x2: e + n,
  y2: t + r
}), $s = ({ x: e, y: t, x2: n, y2: r }) => ({
  x: e,
  y: t,
  width: n - e,
  height: r - t
}), mr = (e, t = [0, 0]) => {
  var o, i;
  const { x: n, y: r } = Ra(e) ? e.internals.positionAbsolute : Ro(e, t);
  return {
    x: n,
    y: r,
    width: ((o = e.measured) == null ? void 0 : o.width) ?? e.width ?? e.initialWidth ?? 0,
    height: ((i = e.measured) == null ? void 0 : i.height) ?? e.height ?? e.initialHeight ?? 0
  };
}, us = (e, t = [0, 0]) => {
  var o, i;
  const { x: n, y: r } = Ra(e) ? e.internals.positionAbsolute : Ro(e, t);
  return {
    x: n,
    y: r,
    x2: n + (((o = e.measured) == null ? void 0 : o.width) ?? e.width ?? e.initialWidth ?? 0),
    y2: r + (((i = e.measured) == null ? void 0 : i.height) ?? e.height ?? e.initialHeight ?? 0)
  };
}, dg = (e, t) => $s(As(Mu(e), Mu(t))), Co = (e, t) => {
  const n = Math.max(0, Math.min(e.x + e.width, t.x + t.width) - Math.max(e.x, t.x)), r = Math.max(0, Math.min(e.y + e.height, t.y + t.height) - Math.max(e.y, t.y));
  return Math.ceil(n * r);
}, zf = (e) => at(e.width) && at(e.height) && at(e.x) && at(e.y), at = (e) => !isNaN(e) && isFinite(e), $S = (e, t) => {
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
function DS(e, t, n) {
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
function RS(e, t, n, r, o, i) {
  const { x: s, y: l } = as(e, [t, n, r]), { x: u, y: a } = as({ x: e.x + e.width, y: e.y + e.height }, [t, n, r]), d = o - u, c = i - a;
  return {
    left: Math.floor(s),
    top: Math.floor(l),
    right: Math.floor(d),
    bottom: Math.floor(c)
  };
}
const Oa = (e, t, n, r, o, i) => {
  const s = DS(i, t, n), l = (t - s.x) / e.width, u = (n - s.y) / e.height, a = Math.min(l, u), d = gr(a, r, o), c = e.x + e.width / 2, f = e.y + e.height / 2, m = t / 2 - c * d, y = n / 2 - f * d, w = RS(e, m, y, d, t, n), x = {
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
function hg(e) {
  var t, n;
  return (((t = e.measured) == null ? void 0 : t.width) ?? e.width ?? e.initialWidth) !== void 0 && (((n = e.measured) == null ? void 0 : n.height) ?? e.height ?? e.initialHeight) !== void 0;
}
function pg(e, t = { width: 0, height: 0 }, n, r, o) {
  const i = { ...e }, s = r.get(n);
  if (s) {
    const l = s.origin || o;
    i.x += s.internals.positionAbsolute.x - (t.width ?? 0) * l[0], i.y += s.internals.positionAbsolute.y - (t.height ?? 0) * l[1];
  }
  return i;
}
function Tf(e, t) {
  if (e.size !== t.size)
    return !1;
  for (const n of e)
    if (!t.has(n))
      return !1;
  return !0;
}
function jS() {
  let e, t;
  return { promise: new Promise((r, o) => {
    e = r, t = o;
  }), resolve: e, reject: t };
}
function OS(e) {
  return { ...ig, ...e || {} };
}
function eo(e, { snapGrid: t = [0, 0], snapToGrid: n = !1, transform: r, containerBounds: o }) {
  const { x: i, y: s } = ct(e), l = Fo({ x: i - ((o == null ? void 0 : o.left) ?? 0), y: s - ((o == null ? void 0 : o.top) ?? 0) }, r), { x: u, y: a } = n ? Oo(l, t) : l;
  return {
    xSnapped: u,
    ySnapped: a,
    ...l
  };
}
const Fa = (e) => ({
  width: e.offsetWidth,
  height: e.offsetHeight
}), gg = (e) => {
  var t;
  return ((t = e == null ? void 0 : e.getRootNode) == null ? void 0 : t.call(e)) || (window == null ? void 0 : window.document);
}, FS = ["INPUT", "SELECT", "TEXTAREA"];
function mg(e) {
  var r, o;
  const t = ((o = (r = e.composedPath) == null ? void 0 : r.call(e)) == null ? void 0 : o[0]) || e.target;
  return (t == null ? void 0 : t.nodeType) !== 1 ? !1 : FS.includes(t.nodeName) || t.hasAttribute("contenteditable") || !!t.closest(".nokey");
}
const yg = (e) => "clientX" in e, ct = (e, t) => {
  var i, s;
  const n = yg(e), r = n ? e.clientX : (i = e.touches) == null ? void 0 : i[0].clientX, o = n ? e.clientY : (s = e.touches) == null ? void 0 : s[0].clientY;
  return {
    x: r - ((t == null ? void 0 : t.left) ?? 0),
    y: o - ((t == null ? void 0 : t.top) ?? 0)
  };
}, Lf = (e, t, n, r, o) => {
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
      ...Fa(s)
    };
  });
};
function vg({ sourceX: e, sourceY: t, targetX: n, targetY: r, sourceControlX: o, sourceControlY: i, targetControlX: s, targetControlY: l }) {
  const u = e * 0.125 + o * 0.375 + s * 0.375 + n * 0.125, a = t * 0.125 + i * 0.375 + l * 0.375 + r * 0.125, d = Math.abs(u - e), c = Math.abs(a - t);
  return [u, a, d, c];
}
function ci(e, t) {
  return e >= 0 ? 0.5 * e : t * 25 * Math.sqrt(-e);
}
function Af({ pos: e, x1: t, y1: n, x2: r, y2: o, c: i }) {
  switch (e) {
    case Q.Left:
      return [t - ci(t - r, i), n];
    case Q.Right:
      return [t + ci(r - t, i), n];
    case Q.Top:
      return [t, n - ci(n - o, i)];
    case Q.Bottom:
      return [t, n + ci(o - n, i)];
  }
}
function wg({ sourceX: e, sourceY: t, sourcePosition: n = Q.Bottom, targetX: r, targetY: o, targetPosition: i = Q.Top, curvature: s = 0.25 }) {
  const [l, u] = Af({
    pos: n,
    x1: e,
    y1: t,
    x2: r,
    y2: o,
    c: s
  }), [a, d] = Af({
    pos: i,
    x1: r,
    y1: o,
    x2: e,
    y2: t,
    c: s
  }), [c, f, m, y] = vg({
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
function xg({ sourceX: e, sourceY: t, targetX: n, targetY: r }) {
  const o = Math.abs(n - e) / 2, i = n < e ? n + o : n - o, s = Math.abs(r - t) / 2, l = r < t ? r + s : r - s;
  return [i, l, o, s];
}
function HS({ sourceNode: e, targetNode: t, selected: n = !1, zIndex: r = 0, elevateOnSelect: o = !1, zIndexMode: i = "basic" }) {
  if (i === "manual")
    return r;
  const s = o && n ? r + 1e3 : r, l = Math.max(e.parentId || o && e.selected ? e.internals.z : 0, t.parentId || o && t.selected ? t.internals.z : 0);
  return s + l;
}
function VS({ sourceNode: e, targetNode: t, width: n, height: r, transform: o }) {
  const i = As(us(e), us(t));
  i.x === i.x2 && (i.x2 += 1), i.y === i.y2 && (i.y2 += 1);
  const s = {
    x: -o[0] / o[2],
    y: -o[1] / o[2],
    width: n / o[2],
    height: r / o[2]
  };
  return Co(s, $s(i)) > 0;
}
const BS = ({ source: e, sourceHandle: t, target: n, targetHandle: r }) => `xy-edge__${e}${t || ""}-${n}${r || ""}`, WS = (e, t) => t.some((n) => n.source === e.source && n.target === e.target && (n.sourceHandle === e.sourceHandle || !n.sourceHandle && !e.sourceHandle) && (n.targetHandle === e.targetHandle || !n.targetHandle && !e.targetHandle)), US = (e, t, n = {}) => {
  if (!e.source || !e.target)
    return t;
  const r = n.getEdgeId || BS;
  let o;
  return ug(e) ? o = { ...e } : o = {
    ...e,
    id: r(e)
  }, WS(o, t) ? t : (o.sourceHandle === null && delete o.sourceHandle, o.targetHandle === null && delete o.targetHandle, t.concat(o));
};
function Sg({ sourceX: e, sourceY: t, targetX: n, targetY: r }) {
  const [o, i, s, l] = xg({
    sourceX: e,
    sourceY: t,
    targetX: n,
    targetY: r
  });
  return [`M ${e},${t}L ${n},${r}`, o, i, s, l];
}
const $f = {
  [Q.Left]: { x: -1, y: 0 },
  [Q.Right]: { x: 1, y: 0 },
  [Q.Top]: { x: 0, y: -1 },
  [Q.Bottom]: { x: 0, y: 1 }
}, YS = ({ source: e, sourcePosition: t = Q.Bottom, target: n }) => t === Q.Left || t === Q.Right ? e.x < n.x ? { x: 1, y: 0 } : { x: -1, y: 0 } : e.y < n.y ? { x: 0, y: 1 } : { x: 0, y: -1 }, Df = (e, t) => Math.sqrt(Math.pow(t.x - e.x, 2) + Math.pow(t.y - e.y, 2));
function XS({ source: e, sourcePosition: t = Q.Bottom, target: n, targetPosition: r = Q.Top, center: o, offset: i, stepPosition: s }) {
  const l = $f[t], u = $f[r], a = { x: e.x + l.x * i, y: e.y + l.y * i }, d = { x: n.x + u.x * i, y: n.y + u.y * i }, c = YS({
    source: a,
    sourcePosition: t,
    target: d
  }), f = c.x !== 0 ? "x" : "y", m = c[f];
  let y = [], w, x;
  const p = { x: 0, y: 0 }, g = { x: 0, y: 0 }, [, , h, v] = xg({
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
      const k = f === "x" ? "y" : "x", A = l[f] === u[k], I = a[k] > d[k], $ = a[k] < d[k];
      (l[f] === 1 && (!A && I || A && $) || l[f] !== 1 && (!A && $ || A && I)) && (y = f === "x" ? P : T);
    }
    const O = { x: a.x + p.x, y: a.y + p.y }, z = { x: d.x + g.x, y: d.y + g.y }, D = Math.max(Math.abs(O.x - y[0].x), Math.abs(z.x - y[0].x)), H = Math.max(Math.abs(O.y - y[0].y), Math.abs(z.y - y[0].y));
    D >= H ? (w = (O.x + z.x) / 2, x = y[0].y) : (w = y[0].x, x = (O.y + z.y) / 2);
  }
  const _ = { x: a.x + p.x, y: a.y + p.y }, E = { x: d.x + g.x, y: d.y + g.y };
  return [[
    e,
    // we only want to add the gapped source/target if they are different from the first/last point to avoid duplicates which can cause issues with the bends
    ..._.x !== y[0].x || _.y !== y[0].y ? [_] : [],
    ...y,
    ...E.x !== y[y.length - 1].x || E.y !== y[y.length - 1].y ? [E] : [],
    n
  ], w, x, h, v];
}
function bS(e, t, n, r) {
  const o = Math.min(Df(e, t) / 2, Df(t, n) / 2, r), { x: i, y: s } = t;
  if (e.x === i && i === n.x || e.y === s && s === n.y)
    return `L${i} ${s}`;
  if (e.y === s) {
    const a = e.x < n.x ? -1 : 1, d = e.y < n.y ? 1 : -1;
    return `L ${i + o * a},${s}Q ${i},${s} ${i},${s + o * d}`;
  }
  const l = e.x < n.x ? 1 : -1, u = e.y < n.y ? -1 : 1;
  return `L ${i},${s + o * u}Q ${i},${s} ${i + o * l},${s}`;
}
function Pu({ sourceX: e, sourceY: t, sourcePosition: n = Q.Bottom, targetX: r, targetY: o, targetPosition: i = Q.Top, borderRadius: s = 5, centerX: l, centerY: u, offset: a = 20, stepPosition: d = 0.5 }) {
  const [c, f, m, y, w] = XS({
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
    x += bS(c[p - 1], c[p], c[p + 1], s);
  return x += `L${c[c.length - 1].x} ${c[c.length - 1].y}`, [x, f, m, y, w];
}
function Rf(e) {
  var t;
  return e && !!(e.internals.handleBounds || (t = e.handles) != null && t.length) && !!(e.measured.width || e.width || e.initialWidth);
}
function QS(e) {
  var c;
  const { sourceNode: t, targetNode: n } = e;
  if (!Rf(t) || !Rf(n))
    return null;
  const r = t.internals.handleBounds || jf(t.handles), o = n.internals.handleBounds || jf(n.handles), i = Of((r == null ? void 0 : r.source) ?? [], e.sourceHandle), s = Of(
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
  const l = (i == null ? void 0 : i.position) || Q.Bottom, u = (s == null ? void 0 : s.position) || Q.Top, a = Tn(t, i, l), d = Tn(n, s, u);
  return {
    sourceX: a.x,
    sourceY: a.y,
    targetX: d.x,
    targetY: d.y,
    sourcePosition: l,
    targetPosition: u
  };
}
function jf(e) {
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
function Tn(e, t, n = Q.Left, r = !1) {
  const o = ((t == null ? void 0 : t.x) ?? 0) + e.internals.positionAbsolute.x, i = ((t == null ? void 0 : t.y) ?? 0) + e.internals.positionAbsolute.y, { width: s, height: l } = t ?? Ot(e);
  if (r)
    return { x: o + s / 2, y: i + l / 2 };
  switch ((t == null ? void 0 : t.position) ?? n) {
    case Q.Top:
      return { x: o + s / 2, y: i };
    case Q.Right:
      return { x: o + s, y: i + l / 2 };
    case Q.Bottom:
      return { x: o + s / 2, y: i + l };
    case Q.Left:
      return { x: o, y: i + l / 2 };
  }
}
function Of(e, t) {
  return e && (t ? e.find((n) => n.id === t) : e[0]) || null;
}
function Iu(e, t) {
  return e ? typeof e == "string" ? e : `${t ? `${t}__` : ""}${Object.keys(e).sort().map((r) => `${r}=${e[r]}`).join("&")}` : "";
}
function KS(e, { id: t, defaultColor: n, defaultMarkerStart: r, defaultMarkerEnd: o }) {
  const i = /* @__PURE__ */ new Set();
  return e.reduce((s, l) => ([l.markerStart || r, l.markerEnd || o].forEach((u) => {
    if (u && typeof u == "object") {
      const a = Iu(u, t);
      i.has(a) || (s.push({ id: a, color: u.color || n, ...u }), i.add(a));
    }
  }), s), []).sort((s, l) => s.id.localeCompare(l.id));
}
const _g = 1e3, GS = 10, Ha = {
  nodeOrigin: [0, 0],
  nodeExtent: Eo,
  elevateNodesOnSelect: !0,
  zIndexMode: "basic",
  defaults: {}
}, ZS = {
  ...Ha,
  checkEquality: !0
};
function Va(e, t) {
  const n = { ...e };
  for (const r in t)
    t[r] !== void 0 && (n[r] = t[r]);
  return n;
}
function qS(e, t, n) {
  const r = Va(Ha, n);
  for (const o of e.values())
    if (o.parentId)
      Wa(o, e, t, r);
    else {
      const i = Ro(o, r.nodeOrigin), s = yr(o.extent) ? o.extent : r.nodeExtent, l = zn(i, s, Ot(o));
      o.internals.positionAbsolute = l;
    }
}
function JS(e, t) {
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
function Ba(e) {
  return e === "manual";
}
function zu(e, t, n, r = {}) {
  var d, c;
  const o = Va(ZS, r), i = { i: 0 }, s = new Map(t), l = o != null && o.elevateNodesOnSelect && !Ba(o.zIndexMode) ? _g : 0;
  let u = e.length > 0, a = !1;
  t.clear(), n.clear();
  for (const f of e) {
    let m = s.get(f.id);
    if (o.checkEquality && f === (m == null ? void 0 : m.internals.userNode))
      t.set(f.id, m);
    else {
      const y = Ro(f, o.nodeOrigin), w = yr(f.extent) ? f.extent : o.nodeExtent, x = zn(y, w, Ot(f));
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
          handleBounds: JS(f, m),
          z: Eg(f, l, o.zIndexMode),
          userNode: f
        }
      }, t.set(f.id, m);
    }
    (m.measured === void 0 || m.measured.width === void 0 || m.measured.height === void 0) && !m.hidden && (u = !1), f.parentId && Wa(m, t, n, r, i), a || (a = f.selected ?? !1);
  }
  return { nodesInitialized: u, hasSelectedNodes: a };
}
function e_(e, t) {
  if (!e.parentId)
    return;
  const n = t.get(e.parentId);
  n ? n.set(e.id, e) : t.set(e.parentId, /* @__PURE__ */ new Map([[e.id, e]]));
}
function Wa(e, t, n, r, o) {
  const { elevateNodesOnSelect: i, nodeOrigin: s, nodeExtent: l, zIndexMode: u } = Va(Ha, r), a = e.parentId, d = t.get(a);
  if (!d) {
    console.warn(`Parent node ${a} not found. Please make sure that parent nodes are in front of their child nodes in the nodes array.`);
    return;
  }
  e_(e, n), o && !d.parentId && d.internals.rootParentIndex === void 0 && u === "auto" && (d.internals.rootParentIndex = ++o.i, d.internals.z = d.internals.z + o.i * GS), o && d.internals.rootParentIndex !== void 0 && (o.i = d.internals.rootParentIndex);
  const c = i && !Ba(u) ? _g : 0, { x: f, y: m, z: y } = t_(e, d, s, l, c, u), { positionAbsolute: w } = e.internals, x = f !== w.x || m !== w.y;
  (x || y !== e.internals.z) && t.set(e.id, {
    ...e,
    internals: {
      ...e.internals,
      positionAbsolute: x ? { x: f, y: m } : w,
      z: y
    }
  });
}
function Eg(e, t, n) {
  const r = at(e.zIndex) ? e.zIndex : 0;
  return Ba(n) ? r : r + (e.selected ? t : 0);
}
function t_(e, t, n, r, o, i) {
  const { x: s, y: l } = t.internals.positionAbsolute, u = Ot(e), a = Ro(e, n), d = yr(e.extent) ? zn(a, e.extent, u) : a;
  let c = zn({ x: s + d.x, y: l + d.y }, r, u);
  e.extent === "parent" && (c = cg(c, u, t));
  const f = Eg(e, o, i), m = t.internals.z ?? 0;
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
    const a = ((s = i.get(l.parentId)) == null ? void 0 : s.expandedRect) ?? mr(u), d = dg(a, l.rect);
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
      e.some((_) => _.id === v.id) || o.push({
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
function n_(e, t, n, r, o, i, s) {
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
    const w = Fa(m.nodeElement), x = y.measured.width !== w.width || y.measured.height !== w.height;
    if (!!(w.width && w.height && (x || !y.internals.handleBounds || m.force))) {
      const g = m.nodeElement.getBoundingClientRect(), h = yr(y.extent) ? y.extent : i;
      let { positionAbsolute: v } = y.internals;
      y.parentId && y.extent === "parent" ? v = cg(v, w, t.get(y.parentId)) : h && (v = zn(v, h, w));
      const _ = {
        ...y,
        measured: w,
        internals: {
          ...y.internals,
          positionAbsolute: v,
          handleBounds: {
            source: Lf("source", m.nodeElement, g, c, y.id),
            target: Lf("target", m.nodeElement, g, c, y.id)
          }
        }
      };
      t.set(y.id, _), y.parentId && Wa(_, t, n, { nodeOrigin: o, zIndexMode: s }), u = !0, x && (a.push({
        id: y.id,
        type: "dimensions",
        dimensions: w
      }), y.expandParent && y.parentId && f.push({
        id: y.id,
        parentId: y.parentId,
        rect: mr(_, o)
      }));
    }
  }
  if (f.length > 0) {
    const m = Ua(f, t, n, o);
    a.push(...m);
  }
  return { changes: a, updatedInternals: u };
}
async function r_({ delta: e, panZoom: t, transform: n, translateExtent: r, width: o, height: i }) {
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
function Ff(e, t, n, r, o, i) {
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
function kg(e, t, n) {
  e.clear(), t.clear();
  for (const r of n) {
    const { source: o, target: i, sourceHandle: s = null, targetHandle: l = null } = r, u = { edgeId: r.id, source: o, target: i, sourceHandle: s, targetHandle: l }, a = `${o}-${s}--${i}-${l}`, d = `${i}-${l}--${o}-${s}`;
    Ff("source", u, d, e, o, s), Ff("target", u, a, e, i, l), t.set(r.id, r);
  }
}
function Ng(e, t) {
  if (!e.parentId)
    return !1;
  const n = t.get(e.parentId);
  return n ? n.selected ? !0 : Ng(n, t) : !1;
}
function Hf(e, t, n) {
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
function o_(e, t, n, r) {
  const o = /* @__PURE__ */ new Map();
  for (const [i, s] of e)
    if ((s.selected || s.id === r) && (!s.parentId || !Ng(s, e)) && (s.draggable || t && typeof s.draggable > "u")) {
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
function Sl({ nodeId: e, dragItems: t, nodeLookup: n, dragging: r = !0 }) {
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
function i_({ dragItems: e, snapGrid: t, x: n, y: r }) {
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
function s_({ onNodeMouseDown: e, getStoreItems: t, onDragStart: n, onDrag: r, onDragStop: o }) {
  let i = { x: null, y: null }, s = 0, l = /* @__PURE__ */ new Map(), u = !1, a = { x: 0, y: 0 }, d = null, c = !1, f = null, m = !1, y = !1, w = null;
  function x({ noDragClassName: g, handleSelector: h, domNode: v, isSelectable: _, nodeId: E, nodeClickDistance: M = 0 }) {
    f = Ue(v);
    function P({ x: D, y: H }) {
      const { nodeLookup: k, nodeExtent: A, snapGrid: I, snapToGrid: $, nodeOrigin: N, onNodeDrag: C, onSelectionDrag: L, onError: F, updateNodePositions: R } = t();
      i = { x: D, y: H };
      let U = !1;
      const W = l.size > 1, X = W && A ? Mu(jo(l)) : null, b = W && $ ? i_({
        dragItems: l,
        snapGrid: I,
        x: D,
        y: H
      }) : null;
      for (const [K, B] of l) {
        if (!k.has(K))
          continue;
        let G = { x: D - B.distance.x, y: H - B.distance.y };
        $ && (G = b ? {
          x: Math.round(G.x + b.x),
          y: Math.round(G.y + b.y)
        } : Oo(G, I));
        let ee = null;
        if (W && A && !B.extent && X) {
          const { positionAbsolute: Z } = B.internals, ie = Z.x - X.x + A[0][0], ue = Z.x + B.measured.width - X.x2 + A[1][0], oe = Z.y - X.y + A[0][1], Pe = Z.y + B.measured.height - X.y2 + A[1][1];
          ee = [
            [ie, oe],
            [ue, Pe]
          ];
        }
        const { position: J, positionAbsolute: q } = ag({
          nodeId: K,
          nextPosition: G,
          nodeLookup: k,
          nodeExtent: ee || A,
          nodeOrigin: N,
          onError: F
        });
        U = U || B.position.x !== J.x || B.position.y !== J.y, B.position = J, B.internals.positionAbsolute = q;
      }
      if (y = y || U, !!U && (R(l, !0), w && (r || C || !E && L))) {
        const [K, B] = Sl({
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
      const [I, $] = fg(a, d, k);
      (I !== 0 || $ !== 0) && (i.x = (i.x ?? 0) - I / D[2], i.y = (i.y ?? 0) - $ / D[2], await H({ x: I, y: $ }) && P(i)), s = requestAnimationFrame(T);
    }
    function O(D) {
      var W;
      const { nodeLookup: H, multiSelectionActive: k, nodesDraggable: A, transform: I, snapGrid: $, snapToGrid: N, selectNodesOnDrag: C, onNodeDragStart: L, onSelectionDragStart: F, unselectNodesAndEdges: R } = t();
      c = !0, (!C || !_) && !k && E && ((W = H.get(E)) != null && W.selected || R()), _ && C && E && (e == null || e(E));
      const U = eo(D.sourceEvent, { transform: I, snapGrid: $, snapToGrid: N, containerBounds: d });
      if (i = U, l = o_(H, A, U, E), l.size > 0 && (n || L || !E && F)) {
        const [X, b] = Sl({
          nodeId: E,
          dragItems: l,
          nodeLookup: H
        });
        n == null || n(D.sourceEvent, l, X, b), L == null || L(D.sourceEvent, X, b), E || F == null || F(D.sourceEvent, b);
      }
    }
    const z = Bp().clickDistance(M).on("start", (D) => {
      const { domNode: H, nodeDragThreshold: k, transform: A, snapGrid: I, snapToGrid: $ } = t();
      d = (H == null ? void 0 : H.getBoundingClientRect()) || null, m = !1, y = !1, w = D.sourceEvent, k === 0 && O(D), i = eo(D.sourceEvent, { transform: A, snapGrid: I, snapToGrid: $, containerBounds: d }), a = ct(D.sourceEvent, d);
    }).on("drag", (D) => {
      const { autoPanOnNodeDrag: H, transform: k, snapGrid: A, snapToGrid: I, nodeDragThreshold: $, nodeLookup: N } = t(), C = eo(D.sourceEvent, { transform: k, snapGrid: A, snapToGrid: I, containerBounds: d });
      if (w = D.sourceEvent, (D.sourceEvent.type === "touchmove" && D.sourceEvent.touches.length > 1 || // if user deletes a node while dragging, we need to abort the drag to prevent errors
      E && !N.has(E)) && (m = !0), !m) {
        if (!u && H && c && (u = !0, T()), !c) {
          const L = ct(D.sourceEvent, d), F = L.x - a.x, R = L.y - a.y;
          Math.sqrt(F * F + R * R) > $ && O(D);
        }
        (i.x !== C.xSnapped || i.y !== C.ySnapped) && l && c && (a = ct(D.sourceEvent, d), P(C));
      }
    }).on("end", (D) => {
      if (!(!c || m) && (u = !1, c = !1, cancelAnimationFrame(s), l.size > 0)) {
        const { nodeLookup: H, updateNodePositions: k, onNodeDragStop: A, onSelectionDragStop: I } = t();
        if (y && (k(l, !1), y = !1), o || A || !E && I) {
          const [$, N] = Sl({
            nodeId: E,
            dragItems: l,
            nodeLookup: H,
            dragging: !1
          });
          o == null || o(D.sourceEvent, l, $, N), A == null || A(D.sourceEvent, $, N), E || I == null || I(D.sourceEvent, N);
        }
      }
    }).filter((D) => {
      const H = D.target;
      return !D.button && (!g || !Hf(H, `.${g}`, v)) && (!h || Hf(H, h, v));
    });
    f.call(z);
  }
  function p() {
    f == null || f.on(".drag", null);
  }
  return {
    update: x,
    destroy: p
  };
}
function l_(e, t, n) {
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
const u_ = 250;
function a_(e, t, n, r) {
  var l, u;
  let o = [], i = 1 / 0;
  const s = l_(e, n, t + u_);
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
function Cg(e, t, n, r, o, i = !1) {
  var a, d, c;
  const s = r.get(e);
  if (!s)
    return null;
  const l = o === "strict" ? (a = s.internals.handleBounds) == null ? void 0 : a[t] : [...((d = s.internals.handleBounds) == null ? void 0 : d.source) ?? [], ...((c = s.internals.handleBounds) == null ? void 0 : c.target) ?? []], u = (n ? l == null ? void 0 : l.find((f) => f.id === n) : l == null ? void 0 : l[0]) ?? null;
  return u && i ? { ...u, ...Tn(s, u, u.position, !0) } : u;
}
function Mg(e, t) {
  return e || (t != null && t.classList.contains("target") ? "target" : t != null && t.classList.contains("source") ? "source" : null);
}
function c_(e, t) {
  let n = null;
  return t ? n = !0 : e && !t && (n = !1), n;
}
const Pg = () => !0;
function f_(e, { connectionMode: t, connectionRadius: n, handleId: r, nodeId: o, edgeUpdaterType: i, isTarget: s, domNode: l, nodeLookup: u, lib: a, autoPanOnConnect: d, flowId: c, panBy: f, cancelConnection: m, onConnectStart: y, onConnect: w, onConnectEnd: x, isValidConnection: p = Pg, onReconnectEnd: g, updateConnection: h, getTransform: v, getFromHandle: _, autoPanSpeed: E, dragThreshold: M = 1, handleDomNode: P }) {
  const T = gg(e.target);
  let O = 0, z;
  const { x: D, y: H } = ct(e), k = Mg(i, P), A = l == null ? void 0 : l.getBoundingClientRect();
  let I = !1;
  if (!A || !k)
    return;
  const $ = Cg(o, k, r, u, t);
  if (!$)
    return;
  let N = ct(e, A), C = !1, L = null, F = !1, R = null;
  function U() {
    if (!d || !A)
      return;
    const [J, q] = fg(N, A, E);
    f({ x: J, y: q }), O = requestAnimationFrame(U);
  }
  const W = {
    ...$,
    nodeId: o,
    type: k,
    position: $.position
  }, X = u.get(o);
  let K = {
    inProgress: !0,
    isValid: null,
    from: Tn(X, W, Q.Left, !0),
    fromHandle: W,
    fromPosition: W.position,
    fromNode: X,
    to: N,
    toHandle: null,
    toPosition: Pf[W.position],
    toNode: null,
    pointer: N
  };
  function B() {
    I = !0, h(K), y == null || y(e, { nodeId: o, handleId: r, handleType: k });
  }
  M === 0 && B();
  function G(J) {
    if (!I) {
      const { x: Pe, y: Ft } = ct(J), kt = Pe - D, fn = Ft - H;
      if (!(kt * kt + fn * fn > M * M))
        return;
      B();
    }
    if (!_() || !W) {
      ee(J);
      return;
    }
    const q = v();
    N = ct(J, A), z = a_(Fo(N, q, !1, [1, 1]), n, u, W), C || (U(), C = !0);
    const Z = Ig(J, {
      handle: z,
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
    R = Z.handleDomNode, L = Z.connection, F = c_(!!z, Z.isValid);
    const ie = u.get(o), ue = ie ? Tn(ie, W, Q.Left, !0) : K.from, oe = {
      ...K,
      from: ue,
      isValid: F,
      to: Z.toHandle && F ? as({ x: Z.toHandle.x, y: Z.toHandle.y }, q) : N,
      toHandle: Z.toHandle,
      toPosition: F && Z.toHandle ? Z.toHandle.position : Pf[W.position],
      toNode: Z.toHandle ? u.get(Z.toHandle.nodeId) : null,
      pointer: N
    };
    h(oe), K = oe;
  }
  function ee(J) {
    if (!("touches" in J && J.touches.length > 0)) {
      if (I) {
        (z || R) && L && F && (w == null || w(L));
        const { inProgress: q, ...Z } = K, ie = {
          ...Z,
          toPosition: K.toHandle ? K.toPosition : null
        };
        x == null || x(J, ie), i && (g == null || g(J, ie));
      }
      m(), cancelAnimationFrame(O), C = !1, F = !1, L = null, R = null, T.removeEventListener("mousemove", G), T.removeEventListener("mouseup", ee), T.removeEventListener("touchmove", G), T.removeEventListener("touchend", ee);
    }
  }
  T.addEventListener("mousemove", G), T.addEventListener("mouseup", ee), T.addEventListener("touchmove", G), T.addEventListener("touchend", ee);
}
function Ig(e, { handle: t, connectionMode: n, fromNodeId: r, fromHandleId: o, fromType: i, doc: s, lib: l, flowId: u, isValidConnection: a = Pg, nodeLookup: d }) {
  const c = i === "target", f = t ? s.querySelector(`.${l}-flow__handle[data-id="${u}-${t == null ? void 0 : t.nodeId}-${t == null ? void 0 : t.id}-${t == null ? void 0 : t.type}"]`) : null, { x: m, y } = ct(e), w = s.elementFromPoint(m, y), x = w != null && w.classList.contains(`${l}-flow__handle`) ? w : f, p = {
    handleDomNode: x,
    isValid: !1,
    connection: null,
    toHandle: null
  };
  if (x) {
    const g = Mg(void 0, x), h = x.getAttribute("data-nodeid"), v = x.getAttribute("data-handleid"), _ = x.classList.contains("connectable"), E = x.classList.contains("connectableend");
    if (!h || !g)
      return p;
    const M = {
      source: c ? h : r,
      sourceHandle: c ? v : o,
      target: c ? r : h,
      targetHandle: c ? o : v
    };
    p.connection = M;
    const T = _ && E && (n === pr.Strict ? c && g === "source" || !c && g === "target" : h !== r || v !== o);
    p.isValid = T && a(M), p.toHandle = Cg(h, g, v, d, n, !0);
  }
  return p;
}
const Tu = {
  onPointerDown: f_,
  isValid: Ig
};
function d_({ domNode: e, panZoom: t, getTransform: n, getViewScale: r }) {
  const o = Ue(e);
  function i({ translateExtent: l, width: u, height: a, zoomStep: d = 1, pannable: c = !0, zoomable: f = !0, inversePan: m = !1 }) {
    const y = (h) => {
      if (h.sourceEvent.type !== "wheel" || !t)
        return;
      const v = n(), _ = h.sourceEvent.ctrlKey && Mo() ? 10 : 1, E = -h.sourceEvent.deltaY * (h.sourceEvent.deltaMode === 1 ? 0.05 : h.sourceEvent.deltaMode ? 1 : 2e-3) * d, M = v[2] * Math.pow(2, E * _);
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
      const _ = [
        h.sourceEvent.clientX ?? h.sourceEvent.touches[0].clientX,
        h.sourceEvent.clientY ?? h.sourceEvent.touches[0].clientY
      ], E = [_[0] - w[0], _[1] - w[1]];
      w = _;
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
    }, g = rg().on("start", x).on("zoom", c ? p : null).on("zoom.wheel", f ? y : null);
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
const Ds = (e) => ({
  x: e.x,
  y: e.y,
  zoom: e.k
}), _l = ({ x: e, y: t, zoom: n }) => Ls.translate(e, t).scale(n), Kn = (e, t) => e.target.closest(`.${t}`), zg = (e, t) => t === 2 && Array.isArray(e) && e.includes(2), h_ = (e) => ((e *= 2) <= 1 ? e * e * e : (e -= 2) * e * e + 2) / 2, El = (e, t = 0, n = h_, r = () => {
}) => {
  const o = typeof t == "number" && t > 0;
  return o || r(), o ? e.transition().duration(t).ease(n).on("end", r) : e;
}, Tg = (e) => {
  const t = e.ctrlKey && Mo() ? 10 : 1;
  return -e.deltaY * (e.deltaMode === 1 ? 0.05 : e.deltaMode ? 1 : 2e-3) * t;
};
function p_({ zoomPanValues: e, noWheelClassName: t, d3Selection: n, d3Zoom: r, panOnScrollMode: o, panOnScrollSpeed: i, zoomOnPinch: s, onPanZoomStart: l, onPanZoom: u, onPanZoomEnd: a }) {
  return (d) => {
    if (Kn(d, t))
      return d.ctrlKey && d.preventDefault(), !1;
    d.preventDefault(), d.stopImmediatePropagation();
    const c = n.property("__zoom").k || 1;
    if (d.ctrlKey && s) {
      const x = st(d), p = Tg(d), g = c * Math.pow(2, p);
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
    const w = Ds(n.property("__zoom"));
    clearTimeout(e.panScrollTimeout), e.isPanScrolling ? (u == null || u(d, w), e.panScrollTimeout = setTimeout(() => {
      a == null || a(d, w), e.isPanScrolling = !1;
    }, 150)) : (e.isPanScrolling = !0, l == null || l(d, w));
  };
}
function g_({ noWheelClassName: e, preventScrolling: t, d3ZoomHandler: n }) {
  return function(r, o) {
    const i = r.type === "wheel", s = !t && i && !r.ctrlKey, l = Kn(r, e);
    if (r.ctrlKey && i && l && r.preventDefault(), s || l)
      return null;
    r.preventDefault(), n.call(this, r, o);
  };
}
function m_({ zoomPanValues: e, onDraggingChange: t, onPanZoomStart: n }) {
  return (r) => {
    var i, s, l;
    if ((i = r.sourceEvent) != null && i.internal)
      return;
    const o = Ds(r.transform);
    e.mouseButton = ((s = r.sourceEvent) == null ? void 0 : s.button) || 0, e.isZoomingOrPanning = !0, e.prevViewport = o, ((l = r.sourceEvent) == null ? void 0 : l.type) === "mousedown" && t(!0), n && (n == null || n(r.sourceEvent, o));
  };
}
function y_({ zoomPanValues: e, panOnDrag: t, onPaneContextMenu: n, onTransformChange: r, onPanZoom: o }) {
  return (i) => {
    var s, l;
    e.usedRightMouseButton = !!(n && zg(t, e.mouseButton ?? 0)), (s = i.sourceEvent) != null && s.sync || r([i.transform.x, i.transform.y, i.transform.k]), o && !((l = i.sourceEvent) != null && l.internal) && (o == null || o(i.sourceEvent, Ds(i.transform)));
  };
}
function v_({ zoomPanValues: e, panOnDrag: t, panOnScroll: n, onDraggingChange: r, onPanZoomEnd: o, onPaneContextMenu: i }) {
  return (s) => {
    var l;
    if (!((l = s.sourceEvent) != null && l.internal) && (e.isZoomingOrPanning = !1, i && zg(t, e.mouseButton ?? 0) && !e.usedRightMouseButton && s.sourceEvent && i(s.sourceEvent), e.usedRightMouseButton = !1, r(!1), o)) {
      const u = Ds(s.transform);
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
function w_({ zoomActivationKeyPressed: e, zoomOnScroll: t, zoomOnPinch: n, panOnDrag: r, panOnScroll: o, zoomOnDoubleClick: i, userSelectionActive: s, noWheelClassName: l, noPanClassName: u, lib: a, connectionInProgress: d }) {
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
function x_({ domNode: e, minZoom: t, maxZoom: n, translateExtent: r, viewport: o, onPanZoom: i, onPanZoomStart: s, onPanZoomEnd: l, onDraggingChange: u }) {
  const a = {
    isZoomingOrPanning: !1,
    usedRightMouseButton: !1,
    prevViewport: {},
    mouseButton: 0,
    timerId: void 0,
    panScrollTimeout: void 0,
    isPanScrolling: !1
  }, d = e.getBoundingClientRect(), c = rg().scaleExtent([t, n]).translateExtent(r), f = Ue(e).call(c);
  g({
    x: o.x,
    y: o.y,
    zoom: gr(o.zoom, t, n)
  }, [
    [0, 0],
    [d.width, d.height]
  ], r);
  const m = f.on("wheel.zoom"), y = f.on("dblclick.zoom");
  c.wheelDelta(Tg);
  function w(z, D) {
    return f ? new Promise((H) => {
      c == null || c.interpolate((D == null ? void 0 : D.interpolate) === "linear" ? Jr : Mi).transform(El(f, D == null ? void 0 : D.duration, D == null ? void 0 : D.ease, () => H(!0)), z);
    }) : Promise.resolve(!1);
  }
  function x({ noWheelClassName: z, noPanClassName: D, onPaneContextMenu: H, userSelectionActive: k, panOnScroll: A, panOnDrag: I, panOnScrollMode: $, panOnScrollSpeed: N, preventScrolling: C, zoomOnPinch: L, zoomOnScroll: F, zoomOnDoubleClick: R, zoomActivationKeyPressed: U, lib: W, onTransformChange: X, connectionInProgress: b, paneClickDistance: K, selectionOnDrag: B }) {
    k && !a.isZoomingOrPanning && p();
    const G = A && !U && !k;
    c.clickDistance(B ? 1 / 0 : !at(K) || K < 0 ? 0 : K);
    const ee = G ? p_({
      zoomPanValues: a,
      noWheelClassName: z,
      d3Selection: f,
      d3Zoom: c,
      panOnScrollMode: $,
      panOnScrollSpeed: N,
      zoomOnPinch: L,
      onPanZoomStart: s,
      onPanZoom: i,
      onPanZoomEnd: l
    }) : g_({
      noWheelClassName: z,
      preventScrolling: C,
      d3ZoomHandler: m
    });
    if (f.on("wheel.zoom", ee, { passive: !1 }), !k) {
      const q = m_({
        zoomPanValues: a,
        onDraggingChange: u,
        onPanZoomStart: s
      });
      c.on("start", q);
      const Z = y_({
        zoomPanValues: a,
        panOnDrag: I,
        onPaneContextMenu: !!H,
        onPanZoom: i,
        onTransformChange: X
      });
      c.on("zoom", Z);
      const ie = v_({
        zoomPanValues: a,
        panOnDrag: I,
        panOnScroll: A,
        onPaneContextMenu: H,
        onPanZoomEnd: l,
        onDraggingChange: u
      });
      c.on("end", ie);
    }
    const J = w_({
      zoomActivationKeyPressed: U,
      panOnDrag: I,
      zoomOnScroll: F,
      panOnScroll: A,
      zoomOnDoubleClick: R,
      zoomOnPinch: L,
      userSelectionActive: k,
      noPanClassName: D,
      noWheelClassName: z,
      lib: W,
      connectionInProgress: b
    });
    c.filter(J), R ? f.on("dblclick.zoom", y) : f.on("dblclick.zoom", null);
  }
  function p() {
    c.on("zoom", null);
  }
  async function g(z, D, H) {
    const k = _l(z), A = c == null ? void 0 : c.constrain()(k, D, H);
    return A && await w(A), new Promise((I) => I(A));
  }
  async function h(z, D) {
    const H = _l(z);
    return await w(H, D), new Promise((k) => k(H));
  }
  function v(z) {
    if (f) {
      const D = _l(z), H = f.property("__zoom");
      (H.k !== z.zoom || H.x !== z.x || H.y !== z.y) && (c == null || c.transform(f, D, null, { sync: !0 }));
    }
  }
  function _() {
    const z = f ? ng(f.node()) : { x: 0, y: 0, k: 1 };
    return { x: z.x, y: z.y, zoom: z.k };
  }
  function E(z, D) {
    return f ? new Promise((H) => {
      c == null || c.interpolate((D == null ? void 0 : D.interpolate) === "linear" ? Jr : Mi).scaleTo(El(f, D == null ? void 0 : D.duration, D == null ? void 0 : D.ease, () => H(!0)), z);
    }) : Promise.resolve(!1);
  }
  function M(z, D) {
    return f ? new Promise((H) => {
      c == null || c.interpolate((D == null ? void 0 : D.interpolate) === "linear" ? Jr : Mi).scaleBy(El(f, D == null ? void 0 : D.duration, D == null ? void 0 : D.ease, () => H(!0)), z);
    }) : Promise.resolve(!1);
  }
  function P(z) {
    c == null || c.scaleExtent(z);
  }
  function T(z) {
    c == null || c.translateExtent(z);
  }
  function O(z) {
    const D = !at(z) || z < 0 ? 0 : z;
    c == null || c.clickDistance(D);
  }
  return {
    update: x,
    destroy: p,
    setViewport: h,
    setViewportConstrained: g,
    getViewport: _,
    scaleTo: E,
    scaleBy: M,
    setScaleExtent: P,
    setTranslateExtent: T,
    syncViewport: v,
    setClickDistance: O
  };
}
var vr;
(function(e) {
  e.Line = "line", e.Handle = "handle";
})(vr || (vr = {}));
function S_({ width: e, prevWidth: t, height: n, prevHeight: r, affectsX: o, affectsY: i }) {
  const s = e - t, l = n - r, u = [s > 0 ? 1 : s < 0 ? -1 : 0, l > 0 ? 1 : l < 0 ? -1 : 0];
  return s && o && (u[0] = u[0] * -1), l && i && (u[1] = u[1] * -1), u;
}
function Vf(e) {
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
function Bf(e, t) {
  return e ? !t : t;
}
function __(e, t, n, r, o, i, s, l) {
  let { affectsX: u, affectsY: a } = t;
  const { isHorizontal: d, isVertical: c } = t, f = d && c, { xSnapped: m, ySnapped: y } = n, { minWidth: w, maxWidth: x, minHeight: p, maxHeight: g } = r, { x: h, y: v, width: _, height: E, aspectRatio: M } = e;
  let P = Math.floor(d ? m - e.pointerX : 0), T = Math.floor(c ? y - e.pointerY : 0);
  const O = _ + (u ? -P : P), z = E + (a ? -T : T), D = -i[0] * _, H = -i[1] * E;
  let k = fi(O, w, x), A = fi(z, p, g);
  if (s) {
    let N = 0, C = 0;
    u && P < 0 ? N = Vt(h + P + D, s[0][0]) : !u && P > 0 && (N = Bt(h + O + D, s[1][0])), a && T < 0 ? C = Vt(v + T + H, s[0][1]) : !a && T > 0 && (C = Bt(v + z + H, s[1][1])), k = Math.max(k, N), A = Math.max(A, C);
  }
  if (l) {
    let N = 0, C = 0;
    u && P > 0 ? N = Bt(h + P, l[0][0]) : !u && P < 0 && (N = Vt(h + O, l[1][0])), a && T > 0 ? C = Bt(v + T, l[0][1]) : !a && T < 0 && (C = Vt(v + z, l[1][1])), k = Math.max(k, N), A = Math.max(A, C);
  }
  if (o) {
    if (d) {
      const N = fi(O / M, p, g) * M;
      if (k = Math.max(k, N), s) {
        let C = 0;
        !u && !a || u && !a && f ? C = Bt(v + H + O / M, s[1][1]) * M : C = Vt(v + H + (u ? P : -P) / M, s[0][1]) * M, k = Math.max(k, C);
      }
      if (l) {
        let C = 0;
        !u && !a || u && !a && f ? C = Vt(v + O / M, l[1][1]) * M : C = Bt(v + (u ? P : -P) / M, l[0][1]) * M, k = Math.max(k, C);
      }
    }
    if (c) {
      const N = fi(z * M, w, x) / M;
      if (A = Math.max(A, N), s) {
        let C = 0;
        !u && !a || a && !u && f ? C = Bt(h + z * M + D, s[1][0]) / M : C = Vt(h + (a ? T : -T) * M + D, s[0][0]) / M, A = Math.max(A, C);
      }
      if (l) {
        let C = 0;
        !u && !a || a && !u && f ? C = Vt(h + z * M, l[1][0]) / M : C = Bt(h + (a ? T : -T) * M, l[0][0]) / M, A = Math.max(A, C);
      }
    }
  }
  T = T + (T < 0 ? A : -A), P = P + (P < 0 ? k : -k), o && (f ? O > z * M ? T = (Bf(u, a) ? -P : P) / M : P = (Bf(u, a) ? -T : T) * M : d ? (T = P / M, a = u) : (P = T * M, u = a));
  const I = u ? h + P : h, $ = a ? v + T : v;
  return {
    width: _ + (u ? -P : P),
    height: E + (a ? -T : T),
    x: i[0] * P * (u ? -1 : 1) + I,
    y: i[1] * T * (a ? -1 : 1) + $
  };
}
const Lg = { width: 0, height: 0, x: 0, y: 0 }, E_ = {
  ...Lg,
  pointerX: 0,
  pointerY: 0,
  aspectRatio: 1
};
function k_(e) {
  return [
    [0, 0],
    [e.measured.width, e.measured.height]
  ];
}
function N_(e, t, n) {
  const r = t.position.x + e.position.x, o = t.position.y + e.position.y, i = e.measured.width ?? 0, s = e.measured.height ?? 0, l = n[0] * i, u = n[1] * s;
  return [
    [r - l, o - u],
    [r + i - l, o + s - u]
  ];
}
function C_({ domNode: e, nodeId: t, getStoreItems: n, onChange: r, onEnd: o }) {
  const i = Ue(e);
  let s = {
    controlDirection: Vf("bottom-right"),
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
    let p = { ...Lg }, g = { ...E_ };
    s = {
      boundaries: d,
      resizeDirection: f,
      keepAspectRatio: c,
      controlDirection: Vf(a)
    };
    let h, v = null, _ = [], E, M, P, T = !1;
    const O = Bp().on("start", (z) => {
      const { nodeLookup: D, transform: H, snapGrid: k, snapToGrid: A, nodeOrigin: I, paneDomNode: $ } = n();
      if (h = D.get(t), !h)
        return;
      v = ($ == null ? void 0 : $.getBoundingClientRect()) ?? null;
      const { xSnapped: N, ySnapped: C } = eo(z.sourceEvent, {
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
      }, E = void 0, h.parentId && (h.extent === "parent" || h.expandParent) && (E = D.get(h.parentId), M = E && h.extent === "parent" ? k_(E) : void 0), _ = [], P = void 0;
      for (const [L, F] of D)
        if (F.parentId === t && (_.push({
          id: L,
          position: { ...F.position },
          extent: F.extent
        }), F.extent === "parent" || F.expandParent)) {
          const R = N_(F, h, F.origin ?? I);
          P ? P = [
            [Math.min(R[0][0], P[0][0]), Math.min(R[0][1], P[0][1])],
            [Math.max(R[1][0], P[1][0]), Math.max(R[1][1], P[1][1])]
          ] : P = R;
        }
      m == null || m(z, { ...p });
    }).on("drag", (z) => {
      const { transform: D, snapGrid: H, snapToGrid: k, nodeOrigin: A } = n(), I = eo(z.sourceEvent, {
        transform: D,
        snapGrid: H,
        snapToGrid: k,
        containerBounds: v
      }), $ = [];
      if (!h)
        return;
      const { x: N, y: C, width: L, height: F } = p, R = {}, U = h.origin ?? A, { width: W, height: X, x: b, y: K } = __(g, s.controlDirection, I, s.boundaries, s.keepAspectRatio, U, M, P), B = W !== L, G = X !== F, ee = b !== N && B, J = K !== C && G;
      if (!ee && !J && !B && !G)
        return;
      if ((ee || J || U[0] === 1 || U[1] === 1) && (R.x = ee ? b : p.x, R.y = J ? K : p.y, p.x = R.x, p.y = R.y, _.length > 0)) {
        const ue = b - N, oe = K - C;
        for (const Pe of _)
          Pe.position = {
            x: Pe.position.x - ue + U[0] * (W - L),
            y: Pe.position.y - oe + U[1] * (X - F)
          }, $.push(Pe);
      }
      if ((B || G) && (R.width = B && (!s.resizeDirection || s.resizeDirection === "horizontal") ? W : p.width, R.height = G && (!s.resizeDirection || s.resizeDirection === "vertical") ? X : p.height, p.width = R.width, p.height = R.height), E && h.expandParent) {
        const ue = U[0] * (R.width ?? 0);
        R.x && R.x < ue && (p.x = ue, g.x = g.x - (R.x - ue));
        const oe = U[1] * (R.height ?? 0);
        R.y && R.y < oe && (p.y = oe, g.y = g.y - (R.y - oe));
      }
      const q = S_({
        width: p.width,
        prevWidth: L,
        height: p.height,
        prevHeight: F,
        affectsX: s.controlDirection.affectsX,
        affectsY: s.controlDirection.affectsY
      }), Z = { ...p, direction: q };
      (x == null ? void 0 : x(z, Z)) !== !1 && (T = !0, y == null || y(z, Z), r(R, $));
    }).on("end", (z) => {
      T && (w == null || w(z, { ...p }), o == null || o({ ...p }), T = !1);
    });
    i.call(O);
  }
  function u() {
    i.on(".drag", null);
  }
  return {
    update: l,
    destroy: u
  };
}
var Ag = { exports: {} }, $g = {}, Dg = { exports: {} }, Rg = {};
/**
 * @license React
 * use-sync-external-store-shim.production.js
 *
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var wr = j;
function M_(e, t) {
  return e === t && (e !== 0 || 1 / e === 1 / t) || e !== e && t !== t;
}
var P_ = typeof Object.is == "function" ? Object.is : M_, I_ = wr.useState, z_ = wr.useEffect, T_ = wr.useLayoutEffect, L_ = wr.useDebugValue;
function A_(e, t) {
  var n = t(), r = I_({ inst: { value: n, getSnapshot: t } }), o = r[0].inst, i = r[1];
  return T_(
    function() {
      o.value = n, o.getSnapshot = t, kl(o) && i({ inst: o });
    },
    [e, n, t]
  ), z_(
    function() {
      return kl(o) && i({ inst: o }), e(function() {
        kl(o) && i({ inst: o });
      });
    },
    [e]
  ), L_(n), n;
}
function kl(e) {
  var t = e.getSnapshot;
  e = e.value;
  try {
    var n = t();
    return !P_(e, n);
  } catch {
    return !0;
  }
}
function $_(e, t) {
  return t();
}
var D_ = typeof window > "u" || typeof window.document > "u" || typeof window.document.createElement > "u" ? $_ : A_;
Rg.useSyncExternalStore = wr.useSyncExternalStore !== void 0 ? wr.useSyncExternalStore : D_;
Dg.exports = Rg;
var R_ = Dg.exports;
/**
 * @license React
 * use-sync-external-store-shim/with-selector.production.js
 *
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var Rs = j, j_ = R_;
function O_(e, t) {
  return e === t && (e !== 0 || 1 / e === 1 / t) || e !== e && t !== t;
}
var F_ = typeof Object.is == "function" ? Object.is : O_, H_ = j_.useSyncExternalStore, V_ = Rs.useRef, B_ = Rs.useEffect, W_ = Rs.useMemo, U_ = Rs.useDebugValue;
$g.useSyncExternalStoreWithSelector = function(e, t, n, r, o) {
  var i = V_(null);
  if (i.current === null) {
    var s = { hasValue: !1, value: null };
    i.current = s;
  } else s = i.current;
  i = W_(
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
        if (y = c, F_(d, m)) return y;
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
  var l = H_(e, i[0], i[1]);
  return B_(
    function() {
      s.hasValue = !0, s.value = l;
    },
    [l]
  ), U_(l), l;
};
Ag.exports = $g;
var Y_ = Ag.exports;
const X_ = /* @__PURE__ */ pd(Y_), b_ = {}, Wf = (e) => {
  let t;
  const n = /* @__PURE__ */ new Set(), r = (d, c) => {
    const f = typeof d == "function" ? d(t) : d;
    if (!Object.is(f, t)) {
      const m = t;
      t = c ?? (typeof f != "object" || f === null) ? f : Object.assign({}, t, f), n.forEach((y) => y(t, m));
    }
  }, o = () => t, u = { setState: r, getState: o, getInitialState: () => a, subscribe: (d) => (n.add(d), () => n.delete(d)), destroy: () => {
    (b_ ? "production" : void 0) !== "production" && console.warn(
      "[DEPRECATED] The `destroy` method will be unsupported in a future version. Instead use unsubscribe function returned by subscribe. Everything will be garbage-collected if store is garbage-collected."
    ), n.clear();
  } }, a = t = e(r, o, u);
  return u;
}, Q_ = (e) => e ? Wf(e) : Wf, { useDebugValue: K_ } = j0, { useSyncExternalStoreWithSelector: G_ } = X_, Z_ = (e) => e;
function jg(e, t = Z_, n) {
  const r = G_(
    e.subscribe,
    e.getState,
    e.getServerState || e.getInitialState,
    t,
    n
  );
  return K_(r), r;
}
const Uf = (e, t) => {
  const n = Q_(e), r = (o, i = t) => jg(n, o, i);
  return Object.assign(r, n), r;
}, q_ = (e, t) => e ? Uf(e, t) : Uf;
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
const js = j.createContext(null), J_ = js.Provider, Og = _t.error001();
function ne(e, t) {
  const n = j.useContext(js);
  if (n === null)
    throw new Error(Og);
  return jg(n, e, t);
}
function he() {
  const e = j.useContext(js);
  if (e === null)
    throw new Error(Og);
  return j.useMemo(() => ({
    getState: e.getState,
    setState: e.setState,
    subscribe: e.subscribe
  }), [e]);
}
const Yf = { display: "none" }, eE = {
  position: "absolute",
  width: 1,
  height: 1,
  margin: -1,
  border: 0,
  padding: 0,
  overflow: "hidden",
  clip: "rect(0px, 0px, 0px, 0px)",
  clipPath: "inset(100%)"
}, Fg = "react-flow__node-desc", Hg = "react-flow__edge-desc", tE = "react-flow__aria-live", nE = (e) => e.ariaLiveMessage, rE = (e) => e.ariaLabelConfig;
function oE({ rfId: e }) {
  const t = ne(nE);
  return S.jsx("div", { id: `${tE}-${e}`, "aria-live": "assertive", "aria-atomic": "true", style: eE, children: t });
}
function iE({ rfId: e, disableKeyboardA11y: t }) {
  const n = ne(rE);
  return S.jsxs(S.Fragment, { children: [S.jsx("div", { id: `${Fg}-${e}`, style: Yf, children: t ? n["node.a11yDescription.default"] : n["node.a11yDescription.keyboardDisabled"] }), S.jsx("div", { id: `${Hg}-${e}`, style: Yf, children: n["edge.a11yDescription.default"] }), !t && S.jsx(oE, { rfId: e })] });
}
const Os = j.forwardRef(({ position: e = "top-left", children: t, className: n, style: r, ...o }, i) => {
  const s = `${e}`.split("-");
  return S.jsx("div", { className: xe(["react-flow__panel", n, ...s]), style: r, ref: i, ...o, children: t });
});
Os.displayName = "Panel";
function sE({ proOptions: e, position: t = "bottom-right" }) {
  return e != null && e.hideAttribution ? null : S.jsx(Os, { position: t, className: "react-flow__attribution", "data-message": "Please only hide this attribution when you are subscribed to React Flow Pro: https://pro.reactflow.dev", children: S.jsx("a", { href: "https://reactflow.dev", target: "_blank", rel: "noopener noreferrer", "aria-label": "React Flow attribution", children: "React Flow" }) });
}
const lE = (e) => {
  const t = [], n = [];
  for (const [, r] of e.nodeLookup)
    r.selected && t.push(r.internals.userNode);
  for (const [, r] of e.edgeLookup)
    r.selected && n.push(r);
  return { selectedNodes: t, selectedEdges: n };
}, di = (e) => e.id;
function uE(e, t) {
  return de(e.selectedNodes.map(di), t.selectedNodes.map(di)) && de(e.selectedEdges.map(di), t.selectedEdges.map(di));
}
function aE({ onSelectionChange: e }) {
  const t = he(), { selectedNodes: n, selectedEdges: r } = ne(lE, uE);
  return j.useEffect(() => {
    const o = { nodes: n, edges: r };
    e == null || e(o), t.getState().onSelectionChangeHandlers.forEach((i) => i(o));
  }, [n, r, e]), null;
}
const cE = (e) => !!e.onSelectionChangeHandlers;
function fE({ onSelectionChange: e }) {
  const t = ne(cE);
  return e || t ? S.jsx(aE, { onSelectionChange: e }) : null;
}
const Lu = typeof window < "u" ? j.useLayoutEffect : j.useEffect, Vg = [0, 0], dE = { x: 0, y: 0, zoom: 1 }, hE = [
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
], Xf = [...hE, "rfId"], pE = (e) => ({
  setNodes: e.setNodes,
  setEdges: e.setEdges,
  setMinZoom: e.setMinZoom,
  setMaxZoom: e.setMaxZoom,
  setTranslateExtent: e.setTranslateExtent,
  setNodeExtent: e.setNodeExtent,
  reset: e.reset,
  setDefaultNodesAndEdges: e.setDefaultNodesAndEdges
}), bf = {
  /*
   * these are values that are also passed directly to other components
   * than the StoreUpdater. We can reduce the number of setStore calls
   * by setting the same values here as prev fields.
   */
  translateExtent: Eo,
  nodeOrigin: Vg,
  minZoom: 0.5,
  maxZoom: 2,
  elementsSelectable: !0,
  noPanClassName: "nopan",
  rfId: "1"
};
function gE(e) {
  const { setNodes: t, setEdges: n, setMinZoom: r, setMaxZoom: o, setTranslateExtent: i, setNodeExtent: s, reset: l, setDefaultNodesAndEdges: u } = ne(pE, de), a = he();
  Lu(() => (u(e.defaultNodes, e.defaultEdges), () => {
    d.current = bf, l();
  }), []);
  const d = j.useRef(bf);
  return Lu(
    () => {
      for (const c of Xf) {
        const f = e[c], m = d.current[c];
        f !== m && (typeof e[c] > "u" || (c === "nodes" ? t(f) : c === "edges" ? n(f) : c === "minZoom" ? r(f) : c === "maxZoom" ? o(f) : c === "translateExtent" ? i(f) : c === "nodeExtent" ? s(f) : c === "ariaLabelConfig" ? a.setState({ ariaLabelConfig: OS(f) }) : c === "fitView" ? a.setState({ fitViewQueued: f }) : c === "fitViewOptions" ? a.setState({ fitViewOptions: f }) : a.setState({ [c]: f })));
      }
      d.current = e;
    },
    // Only re-run the effect if one of the fields we track changes
    Xf.map((c) => e[c])
  ), null;
}
function Qf() {
  return typeof window > "u" || !window.matchMedia ? null : window.matchMedia("(prefers-color-scheme: dark)");
}
function mE(e) {
  var r;
  const [t, n] = j.useState(e === "system" ? null : e);
  return j.useEffect(() => {
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
const Kf = typeof document < "u" ? document : null;
function Po(e = null, t = { target: Kf, actInsideInputWithModifier: !0 }) {
  const [n, r] = j.useState(!1), o = j.useRef(!1), i = j.useRef(/* @__PURE__ */ new Set([])), [s, l] = j.useMemo(() => {
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
  return j.useEffect(() => {
    const u = (t == null ? void 0 : t.target) ?? Kf, a = (t == null ? void 0 : t.actInsideInputWithModifier) ?? !0;
    if (e !== null) {
      const d = (m) => {
        var x, p;
        if (o.current = m.ctrlKey || m.metaKey || m.shiftKey || m.altKey, (!o.current || o.current && !a) && mg(m))
          return !1;
        const w = Zf(m.code, l);
        if (i.current.add(m[w]), Gf(s, i.current, !1)) {
          const g = ((p = (x = m.composedPath) == null ? void 0 : x.call(m)) == null ? void 0 : p[0]) || m.target, h = (g == null ? void 0 : g.nodeName) === "BUTTON" || (g == null ? void 0 : g.nodeName) === "A";
          t.preventDefault !== !1 && (o.current || !h) && m.preventDefault(), r(!0);
        }
      }, c = (m) => {
        const y = Zf(m.code, l);
        Gf(s, i.current, !0) ? (r(!1), i.current.clear()) : i.current.delete(m[y]), m.key === "Meta" && i.current.clear(), o.current = !1;
      }, f = () => {
        i.current.clear(), r(!1);
      };
      return u == null || u.addEventListener("keydown", d), u == null || u.addEventListener("keyup", c), window.addEventListener("blur", f), window.addEventListener("contextmenu", f), () => {
        u == null || u.removeEventListener("keydown", d), u == null || u.removeEventListener("keyup", c), window.removeEventListener("blur", f), window.removeEventListener("contextmenu", f);
      };
    }
  }, [e, r]), n;
}
function Gf(e, t, n) {
  return e.filter((r) => n || r.length === t.size).some((r) => r.every((o) => t.has(o)));
}
function Zf(e, t) {
  return t.includes(e) ? "code" : "key";
}
const yE = () => {
  const e = he();
  return j.useMemo(() => ({
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
      const { width: r, height: o, minZoom: i, maxZoom: s, panZoom: l } = e.getState(), u = Oa(t, r, o, i, s, (n == null ? void 0 : n.padding) ?? 0.1);
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
function Bg(e, t) {
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
      vE(u, l);
    n.push(l);
  }
  return o.length && o.forEach((i) => {
    i.index !== void 0 ? n.splice(i.index, 0, { ...i.item }) : n.push({ ...i.item });
  }), n;
}
function vE(e, t) {
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
function wE(e, t) {
  return Bg(e, t);
}
function xE(e, t) {
  return Bg(e, t);
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
function qf({ items: e = [], lookup: t }) {
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
function Jf(e) {
  return {
    id: e.id,
    type: "remove"
  };
}
const ed = (e) => PS(e), SE = (e) => ug(e);
function Wg(e) {
  return j.forwardRef(e);
}
function td(e) {
  const [t, n] = j.useState(BigInt(0)), [r] = j.useState(() => _E(() => n((o) => o + BigInt(1))));
  return Lu(() => {
    const o = r.get();
    o.length && (e(o), r.reset());
  }, [t]), r;
}
function _E(e) {
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
const Ug = j.createContext(null);
function EE({ children: e }) {
  const t = he(), n = j.useCallback((l) => {
    const { nodes: u = [], setNodes: a, hasDefaultNodes: d, onNodesChange: c, nodeLookup: f, fitViewQueued: m, onNodesChangeMiddlewareMap: y } = t.getState();
    let w = u;
    for (const p of l)
      w = typeof p == "function" ? p(w) : p;
    let x = qf({
      items: w,
      lookup: f
    });
    for (const p of y.values())
      x = p(x);
    d && a(w), x.length > 0 ? c == null || c(x) : m && window.requestAnimationFrame(() => {
      const { fitViewQueued: p, nodes: g, setNodes: h } = t.getState();
      p && h(g);
    });
  }, []), r = td(n), o = j.useCallback((l) => {
    const { edges: u = [], setEdges: a, hasDefaultEdges: d, onEdgesChange: c, edgeLookup: f } = t.getState();
    let m = u;
    for (const y of l)
      m = typeof y == "function" ? y(m) : y;
    d ? a(m) : c && c(qf({
      items: m,
      lookup: f
    }));
  }, []), i = td(o), s = j.useMemo(() => ({ nodeQueue: r, edgeQueue: i }), []);
  return S.jsx(Ug.Provider, { value: s, children: e });
}
function kE() {
  const e = j.useContext(Ug);
  if (!e)
    throw new Error("useBatchContext must be used within a BatchProvider");
  return e;
}
const NE = (e) => !!e.panZoom;
function Fs() {
  const e = yE(), t = he(), n = kE(), r = ne(NE), o = j.useMemo(() => {
    const i = (c) => t.getState().nodeLookup.get(c), s = (c) => {
      n.nodeQueue.push(c);
    }, l = (c) => {
      n.edgeQueue.push(c);
    }, u = (c) => {
      var p, g;
      const { nodeLookup: f, nodeOrigin: m } = t.getState(), y = ed(c) ? c : f.get(c.id), w = y.parentId ? pg(y.position, y.measured, y.parentId, f, m) : y.position, x = {
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
          return m.replace && ed(x) ? x : { ...w, ...x };
        }
        return w;
      }));
    }, d = (c, f, m = { replace: !1 }) => {
      l((y) => y.map((w) => {
        if (w.id === c) {
          const x = typeof f == "function" ? f(w) : f;
          return m.replace && SE(x) ? x : { ...w, ...x };
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
        const { nodes: m, edges: y, onNodesDelete: w, onEdgesDelete: x, triggerNodeChanges: p, triggerEdgeChanges: g, onDelete: h, onBeforeDelete: v } = t.getState(), { nodes: _, edges: E } = await AS({
          nodesToRemove: c,
          edgesToRemove: f,
          nodes: m,
          edges: y,
          onBeforeDelete: v
        }), M = E.length > 0, P = _.length > 0;
        if (M) {
          const T = E.map(Jf);
          x == null || x(E), g(T);
        }
        if (P) {
          const T = _.map(Jf);
          w == null || w(_), p(T);
        }
        return (P || M) && (h == null || h({ nodes: _, edges: E })), { deletedNodes: _, deletedEdges: E };
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
        return IS(c, { nodeLookup: f, nodeOrigin: m });
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
        const f = t.getState().fitViewResolver ?? jS();
        return t.setState({ fitViewQueued: !0, fitViewOptions: c, fitViewResolver: f }), n.nodeQueue.push((m) => [...m]), f.promise;
      }
    };
  }, []);
  return j.useMemo(() => ({
    ...o,
    ...e,
    viewportInitialized: r
  }), [r]);
}
const nd = (e) => e.selected, CE = typeof window < "u" ? window : void 0;
function ME({ deleteKeyCode: e, multiSelectionKeyCode: t }) {
  const n = he(), { deleteElements: r } = Fs(), o = Po(e, { actInsideInputWithModifier: !1 }), i = Po(t, { target: CE });
  j.useEffect(() => {
    if (o) {
      const { edges: s, nodes: l } = n.getState();
      r({ nodes: l.filter(nd), edges: s.filter(nd) }), n.setState({ nodesSelectionActive: !1 });
    }
  }, [o]), j.useEffect(() => {
    n.setState({ multiSelectionActive: i });
  }, [i]);
}
function PE(e) {
  const t = he();
  j.useEffect(() => {
    const n = () => {
      var o, i, s, l;
      if (!e.current || !(((i = (o = e.current).checkVisibility) == null ? void 0 : i.call(o)) ?? !0))
        return !1;
      const r = Fa(e.current);
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
const Hs = {
  position: "absolute",
  width: "100%",
  height: "100%",
  top: 0,
  left: 0
}, IE = (e) => ({
  userSelectionActive: e.userSelectionActive,
  lib: e.lib,
  connectionInProgress: e.connection.inProgress
});
function zE({ onPaneContextMenu: e, zoomOnScroll: t = !0, zoomOnPinch: n = !0, panOnScroll: r = !1, panOnScrollSpeed: o = 0.5, panOnScrollMode: i = _n.Free, zoomOnDoubleClick: s = !0, panOnDrag: l = !0, defaultViewport: u, translateExtent: a, minZoom: d, maxZoom: c, zoomActivationKeyCode: f, preventScrolling: m = !0, children: y, noWheelClassName: w, noPanClassName: x, onViewportChange: p, isControlledViewport: g, paneClickDistance: h, selectionOnDrag: v }) {
  const _ = he(), E = j.useRef(null), { userSelectionActive: M, lib: P, connectionInProgress: T } = ne(IE, de), O = Po(f), z = j.useRef();
  PE(E);
  const D = j.useCallback((H) => {
    p == null || p({ x: H[0], y: H[1], zoom: H[2] }), g || _.setState({ transform: H });
  }, [p, g]);
  return j.useEffect(() => {
    if (E.current) {
      z.current = x_({
        domNode: E.current,
        minZoom: d,
        maxZoom: c,
        translateExtent: a,
        viewport: u,
        onDraggingChange: (I) => _.setState(($) => $.paneDragging === I ? $ : { paneDragging: I }),
        onPanZoomStart: (I, $) => {
          const { onViewportChangeStart: N, onMoveStart: C } = _.getState();
          C == null || C(I, $), N == null || N($);
        },
        onPanZoom: (I, $) => {
          const { onViewportChange: N, onMove: C } = _.getState();
          C == null || C(I, $), N == null || N($);
        },
        onPanZoomEnd: (I, $) => {
          const { onViewportChangeEnd: N, onMoveEnd: C } = _.getState();
          C == null || C(I, $), N == null || N($);
        }
      });
      const { x: H, y: k, zoom: A } = z.current.getViewport();
      return _.setState({
        panZoom: z.current,
        transform: [H, k, A],
        domNode: E.current.closest(".react-flow")
      }), () => {
        var I;
        (I = z.current) == null || I.destroy();
      };
    }
  }, []), j.useEffect(() => {
    var H;
    (H = z.current) == null || H.update({
      onPaneContextMenu: e,
      zoomOnScroll: t,
      zoomOnPinch: n,
      panOnScroll: r,
      panOnScrollSpeed: o,
      panOnScrollMode: i,
      zoomOnDoubleClick: s,
      panOnDrag: l,
      zoomActivationKeyPressed: O,
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
    O,
    m,
    x,
    M,
    w,
    P,
    D,
    T,
    v,
    h
  ]), S.jsx("div", { className: "react-flow__renderer", ref: E, style: Hs, children: y });
}
const TE = (e) => ({
  userSelectionActive: e.userSelectionActive,
  userSelectionRect: e.userSelectionRect
});
function LE() {
  const { userSelectionActive: e, userSelectionRect: t } = ne(TE, de);
  return e && t ? S.jsx("div", { className: "react-flow__selection react-flow__container", style: {
    width: t.width,
    height: t.height,
    transform: `translate(${t.x}px, ${t.y}px)`
  } }) : null;
}
const Nl = (e, t) => (n) => {
  n.target === t.current && (e == null || e(n));
}, AE = (e) => ({
  userSelectionActive: e.userSelectionActive,
  elementsSelectable: e.elementsSelectable,
  connectionInProgress: e.connection.inProgress,
  dragging: e.paneDragging
});
function $E({ isSelecting: e, selectionKeyPressed: t, selectionMode: n = ko.Full, panOnDrag: r, paneClickDistance: o, selectionOnDrag: i, onSelectionStart: s, onSelectionEnd: l, onPaneClick: u, onPaneContextMenu: a, onPaneScroll: d, onPaneMouseEnter: c, onPaneMouseMove: f, onPaneMouseLeave: m, children: y }) {
  const w = he(), { userSelectionActive: x, elementsSelectable: p, dragging: g, connectionInProgress: h } = ne(AE, de), v = p && (e || x), _ = j.useRef(null), E = j.useRef(), M = j.useRef(/* @__PURE__ */ new Set()), P = j.useRef(/* @__PURE__ */ new Set()), T = j.useRef(!1), O = (N) => {
    if (T.current || h) {
      T.current = !1;
      return;
    }
    u == null || u(N), w.getState().resetSelectedElements(), w.setState({ nodesSelectionActive: !1 });
  }, z = (N) => {
    if (Array.isArray(r) && (r != null && r.includes(2))) {
      N.preventDefault();
      return;
    }
    a == null || a(N);
  }, D = d ? (N) => d(N) : void 0, H = (N) => {
    T.current && (N.stopPropagation(), T.current = !1);
  }, k = (N) => {
    var X, b;
    const { domNode: C } = w.getState();
    if (E.current = C == null ? void 0 : C.getBoundingClientRect(), !E.current)
      return;
    const L = N.target === _.current;
    if (!L && !!N.target.closest(".nokey") || !e || !(i && L || t) || N.button !== 0 || !N.isPrimary)
      return;
    (b = (X = N.target) == null ? void 0 : X.setPointerCapture) == null || b.call(X, N.pointerId), T.current = !1;
    const { x: U, y: W } = ct(N.nativeEvent, E.current);
    w.setState({
      userSelectionRect: {
        width: 0,
        height: 0,
        startX: U,
        startY: W,
        x: U,
        y: W
      }
    }), L || (N.stopPropagation(), N.preventDefault());
  }, A = (N) => {
    const { userSelectionRect: C, transform: L, nodeLookup: F, edgeLookup: R, connectionLookup: U, triggerNodeChanges: W, triggerEdgeChanges: X, defaultEdgeOptions: b, resetSelectedElements: K } = w.getState();
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
    M.current = new Set(ja(F, q, L, n === ko.Partial, !0).map((oe) => oe.id)), P.current = /* @__PURE__ */ new Set();
    const ue = (b == null ? void 0 : b.selectable) ?? !0;
    for (const oe of M.current) {
      const Pe = U.get(oe);
      if (Pe)
        for (const { edgeId: Ft } of Pe.values()) {
          const kt = R.get(Ft);
          kt && (kt.selectable ?? ue) && P.current.add(Ft);
        }
    }
    if (!Tf(Z, M.current)) {
      const oe = Gn(F, M.current, !0);
      W(oe);
    }
    if (!Tf(ie, P.current)) {
      const oe = Gn(R, P.current);
      X(oe);
    }
    w.setState({
      userSelectionRect: q,
      userSelectionActive: !0,
      nodesSelectionActive: !1
    });
  }, I = (N) => {
    var C, L;
    N.button === 0 && ((L = (C = N.target) == null ? void 0 : C.releasePointerCapture) == null || L.call(C, N.pointerId), !x && N.target === _.current && w.getState().userSelectionRect && (O == null || O(N)), w.setState({
      userSelectionActive: !1,
      userSelectionRect: null
    }), T.current && (l == null || l(N), w.setState({
      nodesSelectionActive: M.current.size > 0
    })));
  }, $ = r === !0 || Array.isArray(r) && r.includes(0);
  return S.jsxs("div", { className: xe(["react-flow__pane", { draggable: $, dragging: g, selection: e }]), onClick: v ? void 0 : Nl(O, _), onContextMenu: Nl(z, _), onWheel: Nl(D, _), onPointerEnter: v ? void 0 : c, onPointerMove: v ? A : f, onPointerUp: v ? I : void 0, onPointerDownCapture: v ? k : void 0, onClickCapture: v ? H : void 0, onPointerLeave: m, ref: _, style: Hs, children: [y, S.jsx(LE, {})] });
}
function Au({ id: e, store: t, unselect: n = !1, nodeRef: r }) {
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
function Yg({ nodeRef: e, disabled: t = !1, noDragClassName: n, handleSelector: r, nodeId: o, isSelectable: i, nodeClickDistance: s }) {
  const l = he(), [u, a] = j.useState(!1), d = j.useRef();
  return j.useEffect(() => {
    d.current = s_({
      getStoreItems: () => l.getState(),
      onNodeMouseDown: (c) => {
        Au({
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
  }, []), j.useEffect(() => {
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
const DE = (e) => (t) => t.selected && (t.draggable || e && typeof t.draggable > "u");
function Xg() {
  const e = he();
  return j.useCallback((n) => {
    const { nodeExtent: r, snapToGrid: o, snapGrid: i, nodesDraggable: s, onError: l, updateNodePositions: u, nodeLookup: a, nodeOrigin: d } = e.getState(), c = /* @__PURE__ */ new Map(), f = DE(s), m = o ? i[0] : 5, y = o ? i[1] : 5, w = n.direction.x * m * n.factor, x = n.direction.y * y * n.factor;
    for (const [, p] of a) {
      if (!f(p))
        continue;
      let g = {
        x: p.internals.positionAbsolute.x + w,
        y: p.internals.positionAbsolute.y + x
      };
      o && (g = Oo(g, i));
      const { position: h, positionAbsolute: v } = ag({
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
const Ya = j.createContext(null), RE = Ya.Provider;
Ya.Consumer;
const bg = () => j.useContext(Ya), jE = (e) => ({
  connectOnClick: e.connectOnClick,
  noPanClassName: e.noPanClassName,
  rfId: e.rfId
}), OE = (e, t, n) => (r) => {
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
function FE({ type: e = "source", position: t = Q.Top, isValidConnection: n, isConnectable: r = !0, isConnectableStart: o = !0, isConnectableEnd: i = !0, id: s, onConnect: l, children: u, className: a, onMouseDown: d, onTouchStart: c, ...f }, m) {
  var A, I;
  const y = s || null, w = e === "target", x = he(), p = bg(), { connectOnClick: g, noPanClassName: h, rfId: v } = ne(jE, de), { connectingFrom: _, connectingTo: E, clickConnecting: M, isPossibleEndHandle: P, connectionInProcess: T, clickConnectionInProcess: O, valid: z } = ne(OE(p, y, e), de);
  p || (I = (A = x.getState()).onError) == null || I.call(A, "010", _t.error010());
  const D = ($) => {
    const { defaultEdgeOptions: N, onConnect: C, hasDefaultEdges: L } = x.getState(), F = {
      ...N,
      ...$
    };
    if (L) {
      const { edges: R, setEdges: U } = x.getState();
      U(US(F, R));
    }
    C == null || C(F), l == null || l(F);
  }, H = ($) => {
    if (!p)
      return;
    const N = yg($.nativeEvent);
    if (o && (N && $.button === 0 || !N)) {
      const C = x.getState();
      Tu.onPointerDown($.nativeEvent, {
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
          var F, R;
          return (R = (F = x.getState()).onConnectEnd) == null ? void 0 : R.call(F, ...L);
        },
        updateConnection: C.updateConnection,
        onConnect: D,
        isValidConnection: n || ((...L) => {
          var F, R;
          return ((R = (F = x.getState()).isValidConnection) == null ? void 0 : R.call(F, ...L)) ?? !0;
        }),
        getTransform: () => x.getState().transform,
        getFromHandle: () => x.getState().connection.fromHandle,
        autoPanSpeed: C.autoPanSpeed,
        dragThreshold: C.connectionDragThreshold
      });
    }
    N ? d == null || d($) : c == null || c($);
  }, k = ($) => {
    const { onClickConnectStart: N, onClickConnectEnd: C, connectionClickStartHandle: L, connectionMode: F, isValidConnection: R, lib: U, rfId: W, nodeLookup: X, connection: b } = x.getState();
    if (!p || !L && !o)
      return;
    if (!L) {
      N == null || N($.nativeEvent, { nodeId: p, handleId: y, handleType: e }), x.setState({ connectionClickStartHandle: { nodeId: p, type: e, id: y } });
      return;
    }
    const K = gg($.target), B = n || R, { connection: G, isValid: ee } = Tu.isValid($.nativeEvent, {
      handle: {
        nodeId: p,
        id: y,
        type: e
      },
      connectionMode: F,
      fromNodeId: L.nodeId,
      fromHandleId: L.id || null,
      fromType: L.type,
      isValidConnection: B,
      flowId: W,
      doc: K,
      lib: U,
      nodeLookup: X
    });
    ee && G && D(G);
    const J = structuredClone(b);
    delete J.inProgress, J.toPosition = J.toHandle ? J.toHandle.position : null, C == null || C($, J), x.setState({ connectionClickStartHandle: null });
  };
  return S.jsx("div", { "data-handleid": y, "data-nodeid": p, "data-handlepos": t, "data-id": `${v}-${p}-${y}-${e}`, className: xe([
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
      connectingfrom: _,
      connectingto: E,
      valid: z,
      /*
       * shows where you can start a connection from
       * and where you can end it while connecting
       */
      connectionindicator: r && (!T || P) && (T || O ? i : o)
    }
  ]), onMouseDown: H, onTouchStart: H, onClick: g ? k : void 0, ref: m, ...f, children: u });
}
const xr = j.memo(Wg(FE));
function HE({ data: e, isConnectable: t, sourcePosition: n = Q.Bottom }) {
  return S.jsxs(S.Fragment, { children: [e == null ? void 0 : e.label, S.jsx(xr, { type: "source", position: n, isConnectable: t })] });
}
function VE({ data: e, isConnectable: t, targetPosition: n = Q.Top, sourcePosition: r = Q.Bottom }) {
  return S.jsxs(S.Fragment, { children: [S.jsx(xr, { type: "target", position: n, isConnectable: t }), e == null ? void 0 : e.label, S.jsx(xr, { type: "source", position: r, isConnectable: t })] });
}
function BE() {
  return null;
}
function WE({ data: e, isConnectable: t, targetPosition: n = Q.Top }) {
  return S.jsxs(S.Fragment, { children: [S.jsx(xr, { type: "target", position: n, isConnectable: t }), e == null ? void 0 : e.label] });
}
const cs = {
  ArrowUp: { x: 0, y: -1 },
  ArrowDown: { x: 0, y: 1 },
  ArrowLeft: { x: -1, y: 0 },
  ArrowRight: { x: 1, y: 0 }
}, rd = {
  input: HE,
  default: VE,
  output: WE,
  group: BE
};
function UE(e) {
  var t, n, r, o;
  return e.internals.handleBounds === void 0 ? {
    width: e.width ?? e.initialWidth ?? ((t = e.style) == null ? void 0 : t.width),
    height: e.height ?? e.initialHeight ?? ((n = e.style) == null ? void 0 : n.height)
  } : {
    width: e.width ?? ((r = e.style) == null ? void 0 : r.width),
    height: e.height ?? ((o = e.style) == null ? void 0 : o.height)
  };
}
const YE = (e) => {
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
function XE({ onSelectionContextMenu: e, noPanClassName: t, disableKeyboardA11y: n }) {
  const r = he(), { width: o, height: i, transformString: s, userSelectionActive: l } = ne(YE, de), u = Xg(), a = j.useRef(null);
  j.useEffect(() => {
    var m;
    n || (m = a.current) == null || m.focus({
      preventScroll: !0
    });
  }, [n]);
  const d = !l && o !== null && i !== null;
  if (Yg({
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
  return S.jsx("div", { className: xe(["react-flow__nodesselection", "react-flow__container", t]), style: {
    transform: s
  }, children: S.jsx("div", { ref: a, className: "react-flow__nodesselection-rect", onContextMenu: c, tabIndex: n ? void 0 : -1, onKeyDown: n ? void 0 : f, style: {
    width: o,
    height: i
  } }) });
}
const od = typeof window < "u" ? window : void 0, bE = (e) => ({ nodesSelectionActive: e.nodesSelectionActive, userSelectionActive: e.userSelectionActive });
function Qg({ children: e, onPaneClick: t, onPaneMouseEnter: n, onPaneMouseMove: r, onPaneMouseLeave: o, onPaneContextMenu: i, onPaneScroll: s, paneClickDistance: l, deleteKeyCode: u, selectionKeyCode: a, selectionOnDrag: d, selectionMode: c, onSelectionStart: f, onSelectionEnd: m, multiSelectionKeyCode: y, panActivationKeyCode: w, zoomActivationKeyCode: x, elementsSelectable: p, zoomOnScroll: g, zoomOnPinch: h, panOnScroll: v, panOnScrollSpeed: _, panOnScrollMode: E, zoomOnDoubleClick: M, panOnDrag: P, defaultViewport: T, translateExtent: O, minZoom: z, maxZoom: D, preventScrolling: H, onSelectionContextMenu: k, noWheelClassName: A, noPanClassName: I, disableKeyboardA11y: $, onViewportChange: N, isControlledViewport: C }) {
  const { nodesSelectionActive: L, userSelectionActive: F } = ne(bE, de), R = Po(a, { target: od }), U = Po(w, { target: od }), W = U || P, X = U || v, b = d && W !== !0, K = R || F || b;
  return ME({ deleteKeyCode: u, multiSelectionKeyCode: y }), S.jsx(zE, { onPaneContextMenu: i, elementsSelectable: p, zoomOnScroll: g, zoomOnPinch: h, panOnScroll: X, panOnScrollSpeed: _, panOnScrollMode: E, zoomOnDoubleClick: M, panOnDrag: !R && W, defaultViewport: T, translateExtent: O, minZoom: z, maxZoom: D, zoomActivationKeyCode: x, preventScrolling: H, noWheelClassName: A, noPanClassName: I, onViewportChange: N, isControlledViewport: C, paneClickDistance: l, selectionOnDrag: b, children: S.jsxs($E, { onSelectionStart: f, onSelectionEnd: m, onPaneClick: t, onPaneMouseEnter: n, onPaneMouseMove: r, onPaneMouseLeave: o, onPaneContextMenu: i, onPaneScroll: s, panOnDrag: W, isSelecting: !!K, selectionMode: c, selectionKeyPressed: R, paneClickDistance: l, selectionOnDrag: b, children: [e, L && S.jsx(XE, { onSelectionContextMenu: k, noPanClassName: I, disableKeyboardA11y: $ })] }) });
}
Qg.displayName = "FlowRenderer";
const QE = j.memo(Qg), KE = (e) => (t) => e ? ja(t.nodeLookup, { x: 0, y: 0, width: t.width, height: t.height }, t.transform, !0).map((n) => n.id) : Array.from(t.nodeLookup.keys());
function GE(e) {
  return ne(j.useCallback(KE(e), [e]), de);
}
const ZE = (e) => e.updateNodeInternals;
function qE() {
  const e = ne(ZE), [t] = j.useState(() => typeof ResizeObserver > "u" ? null : new ResizeObserver((n) => {
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
  return j.useEffect(() => () => {
    t == null || t.disconnect();
  }, [t]), t;
}
function JE({ node: e, nodeType: t, hasDimensions: n, resizeObserver: r }) {
  const o = he(), i = j.useRef(null), s = j.useRef(null), l = j.useRef(e.sourcePosition), u = j.useRef(e.targetPosition), a = j.useRef(t), d = n && !!e.internals.handleBounds;
  return j.useEffect(() => {
    i.current && !e.hidden && (!d || s.current !== i.current) && (s.current && (r == null || r.unobserve(s.current)), r == null || r.observe(i.current), s.current = i.current);
  }, [d, e.hidden]), j.useEffect(() => () => {
    s.current && (r == null || r.unobserve(s.current), s.current = null);
  }, []), j.useEffect(() => {
    if (i.current) {
      const c = a.current !== t, f = l.current !== e.sourcePosition, m = u.current !== e.targetPosition;
      (c || f || m) && (a.current = t, l.current = e.sourcePosition, u.current = e.targetPosition, o.getState().updateNodeInternals(/* @__PURE__ */ new Map([[e.id, { id: e.id, nodeElement: i.current, force: !0 }]])));
    }
  }, [e.id, t, e.sourcePosition, e.targetPosition]), i;
}
function e2({ id: e, onClick: t, onMouseEnter: n, onMouseMove: r, onMouseLeave: o, onContextMenu: i, onDoubleClick: s, nodesDraggable: l, elementsSelectable: u, nodesConnectable: a, nodesFocusable: d, resizeObserver: c, noDragClassName: f, noPanClassName: m, disableKeyboardA11y: y, rfId: w, nodeTypes: x, nodeClickDistance: p, onError: g }) {
  const { node: h, internals: v, isParent: _ } = ne((B) => {
    const G = B.nodeLookup.get(e), ee = B.parentLookup.has(e);
    return {
      node: G,
      internals: G.internals,
      isParent: ee
    };
  }, de);
  let E = h.type || "default", M = (x == null ? void 0 : x[E]) || rd[E];
  M === void 0 && (g == null || g("003", _t.error003(E)), E = "default", M = (x == null ? void 0 : x.default) || rd.default);
  const P = !!(h.draggable || l && typeof h.draggable > "u"), T = !!(h.selectable || u && typeof h.selectable > "u"), O = !!(h.connectable || a && typeof h.connectable > "u"), z = !!(h.focusable || d && typeof h.focusable > "u"), D = he(), H = hg(h), k = JE({ node: h, nodeType: E, hasDimensions: H, resizeObserver: c }), A = Yg({
    nodeRef: k,
    disabled: h.hidden || !P,
    noDragClassName: f,
    handleSelector: h.dragHandle,
    nodeId: e,
    isSelectable: T,
    nodeClickDistance: p
  }), I = Xg();
  if (h.hidden)
    return null;
  const $ = Ot(h), N = UE(h), C = T || P || t || n || r || o, L = n ? (B) => n(B, { ...v.userNode }) : void 0, F = r ? (B) => r(B, { ...v.userNode }) : void 0, R = o ? (B) => o(B, { ...v.userNode }) : void 0, U = i ? (B) => i(B, { ...v.userNode }) : void 0, W = s ? (B) => s(B, { ...v.userNode }) : void 0, X = (B) => {
    const { selectNodesOnDrag: G, nodeDragThreshold: ee } = D.getState();
    T && (!G || !P || ee > 0) && Au({
      id: e,
      store: D,
      nodeRef: k
    }), t && t(B, { ...v.userNode });
  }, b = (B) => {
    if (!(mg(B.nativeEvent) || y)) {
      if (og.includes(B.key) && T) {
        const G = B.key === "Escape";
        Au({
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
        }), I({
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
    ja(/* @__PURE__ */ new Map([[e, h]]), { x: 0, y: 0, width: G, height: ee }, B, !0).length > 0 || q(h.position.x + $.width / 2, h.position.y + $.height / 2, {
      zoom: B[2]
    });
  };
  return S.jsx("div", { className: xe([
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
      parent: _,
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
  }, "data-id": e, "data-testid": `rf__node-${e}`, onMouseEnter: L, onMouseMove: F, onMouseLeave: R, onContextMenu: U, onClick: X, onDoubleClick: W, onKeyDown: z ? b : void 0, tabIndex: z ? 0 : void 0, onFocus: z ? K : void 0, role: h.ariaRole ?? (z ? "group" : void 0), "aria-roledescription": "node", "aria-describedby": y ? void 0 : `${Fg}-${w}`, "aria-label": h.ariaLabel, ...h.domAttributes, children: S.jsx(RE, { value: e, children: S.jsx(M, { id: e, data: h.data, type: E, positionAbsoluteX: v.positionAbsolute.x, positionAbsoluteY: v.positionAbsolute.y, selected: h.selected ?? !1, selectable: T, draggable: P, deletable: h.deletable ?? !0, isConnectable: O, sourcePosition: h.sourcePosition, targetPosition: h.targetPosition, dragging: A, dragHandle: h.dragHandle, zIndex: v.z, parentId: h.parentId, ...$ }) }) });
}
var t2 = j.memo(e2);
const n2 = (e) => ({
  nodesDraggable: e.nodesDraggable,
  nodesConnectable: e.nodesConnectable,
  nodesFocusable: e.nodesFocusable,
  elementsSelectable: e.elementsSelectable,
  onError: e.onError
});
function Kg(e) {
  const { nodesDraggable: t, nodesConnectable: n, nodesFocusable: r, elementsSelectable: o, onError: i } = ne(n2, de), s = GE(e.onlyRenderVisibleElements), l = qE();
  return S.jsx("div", { className: "react-flow__nodes", style: Hs, children: s.map((u) => (
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
    S.jsx(t2, { id: u, nodeTypes: e.nodeTypes, nodeExtent: e.nodeExtent, onClick: e.onNodeClick, onMouseEnter: e.onNodeMouseEnter, onMouseMove: e.onNodeMouseMove, onMouseLeave: e.onNodeMouseLeave, onContextMenu: e.onNodeContextMenu, onDoubleClick: e.onNodeDoubleClick, noDragClassName: e.noDragClassName, noPanClassName: e.noPanClassName, rfId: e.rfId, disableKeyboardA11y: e.disableKeyboardA11y, resizeObserver: l, nodesDraggable: t, nodesConnectable: n, nodesFocusable: r, elementsSelectable: o, nodeClickDistance: e.nodeClickDistance, onError: i }, u)
  )) });
}
Kg.displayName = "NodeRenderer";
const r2 = j.memo(Kg);
function o2(e) {
  return ne(j.useCallback((n) => {
    if (!e)
      return n.edges.map((o) => o.id);
    const r = [];
    if (n.width && n.height)
      for (const o of n.edges) {
        const i = n.nodeLookup.get(o.source), s = n.nodeLookup.get(o.target);
        i && s && VS({
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
const i2 = ({ color: e = "none", strokeWidth: t = 1 }) => {
  const n = {
    strokeWidth: t,
    ...e && { stroke: e }
  };
  return S.jsx("polyline", { className: "arrow", style: n, strokeLinecap: "round", fill: "none", strokeLinejoin: "round", points: "-5,-4 0,0 -5,4" });
}, s2 = ({ color: e = "none", strokeWidth: t = 1 }) => {
  const n = {
    strokeWidth: t,
    ...e && { stroke: e, fill: e }
  };
  return S.jsx("polyline", { className: "arrowclosed", style: n, strokeLinecap: "round", strokeLinejoin: "round", points: "-5,-4 0,0 -5,4 -5,-4" });
}, id = {
  [No.Arrow]: i2,
  [No.ArrowClosed]: s2
};
function l2(e) {
  const t = he();
  return j.useMemo(() => {
    var o, i;
    return Object.prototype.hasOwnProperty.call(id, e) ? id[e] : ((i = (o = t.getState()).onError) == null || i.call(o, "009", _t.error009(e)), null);
  }, [e]);
}
const u2 = ({ id: e, type: t, color: n, width: r = 12.5, height: o = 12.5, markerUnits: i = "strokeWidth", strokeWidth: s, orient: l = "auto-start-reverse" }) => {
  const u = l2(t);
  return u ? S.jsx("marker", { className: "react-flow__arrowhead", id: e, markerWidth: `${r}`, markerHeight: `${o}`, viewBox: "-10 -10 20 20", markerUnits: i, orient: l, refX: "0", refY: "0", children: S.jsx(u, { color: n, strokeWidth: s }) }) : null;
}, Gg = ({ defaultColor: e, rfId: t }) => {
  const n = ne((i) => i.edges), r = ne((i) => i.defaultEdgeOptions), o = j.useMemo(() => KS(n, {
    id: t,
    defaultColor: e,
    defaultMarkerStart: r == null ? void 0 : r.markerStart,
    defaultMarkerEnd: r == null ? void 0 : r.markerEnd
  }), [n, r, t, e]);
  return o.length ? S.jsx("svg", { className: "react-flow__marker", "aria-hidden": "true", children: S.jsx("defs", { children: o.map((i) => S.jsx(u2, { id: i.id, type: i.type, color: i.color, width: i.width, height: i.height, markerUnits: i.markerUnits, strokeWidth: i.strokeWidth, orient: i.orient }, i.id)) }) }) : null;
};
Gg.displayName = "MarkerDefinitions";
var a2 = j.memo(Gg);
function Zg({ x: e, y: t, label: n, labelStyle: r, labelShowBg: o = !0, labelBgStyle: i, labelBgPadding: s = [2, 4], labelBgBorderRadius: l = 2, children: u, className: a, ...d }) {
  const [c, f] = j.useState({ x: 1, y: 0, width: 0, height: 0 }), m = xe(["react-flow__edge-textwrapper", a]), y = j.useRef(null);
  return j.useEffect(() => {
    if (y.current) {
      const w = y.current.getBBox();
      f({
        x: w.x,
        y: w.y,
        width: w.width,
        height: w.height
      });
    }
  }, [n]), n ? S.jsxs("g", { transform: `translate(${e - c.width / 2} ${t - c.height / 2})`, className: m, visibility: c.width ? "visible" : "hidden", ...d, children: [o && S.jsx("rect", { width: c.width + 2 * s[0], x: -s[0], y: -s[1], height: c.height + 2 * s[1], className: "react-flow__edge-textbg", style: i, rx: l, ry: l }), S.jsx("text", { className: "react-flow__edge-text", y: c.height / 2, dy: "0.3em", ref: y, style: r, children: n }), u] }) : null;
}
Zg.displayName = "EdgeText";
const c2 = j.memo(Zg);
function Vs({ path: e, labelX: t, labelY: n, label: r, labelStyle: o, labelShowBg: i, labelBgStyle: s, labelBgPadding: l, labelBgBorderRadius: u, interactionWidth: a = 20, ...d }) {
  return S.jsxs(S.Fragment, { children: [S.jsx("path", { ...d, d: e, fill: "none", className: xe(["react-flow__edge-path", d.className]) }), a ? S.jsx("path", { d: e, fill: "none", strokeOpacity: 0, strokeWidth: a, className: "react-flow__edge-interaction" }) : null, r && at(t) && at(n) ? S.jsx(c2, { x: t, y: n, label: r, labelStyle: o, labelShowBg: i, labelBgStyle: s, labelBgPadding: l, labelBgBorderRadius: u }) : null] });
}
function sd({ pos: e, x1: t, y1: n, x2: r, y2: o }) {
  return e === Q.Left || e === Q.Right ? [0.5 * (t + r), n] : [t, 0.5 * (n + o)];
}
function qg({ sourceX: e, sourceY: t, sourcePosition: n = Q.Bottom, targetX: r, targetY: o, targetPosition: i = Q.Top }) {
  const [s, l] = sd({
    pos: n,
    x1: e,
    y1: t,
    x2: r,
    y2: o
  }), [u, a] = sd({
    pos: i,
    x1: r,
    y1: o,
    x2: e,
    y2: t
  }), [d, c, f, m] = vg({
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
function Jg(e) {
  return j.memo(({ id: t, sourceX: n, sourceY: r, targetX: o, targetY: i, sourcePosition: s, targetPosition: l, label: u, labelStyle: a, labelShowBg: d, labelBgStyle: c, labelBgPadding: f, labelBgBorderRadius: m, style: y, markerEnd: w, markerStart: x, interactionWidth: p }) => {
    const [g, h, v] = qg({
      sourceX: n,
      sourceY: r,
      sourcePosition: s,
      targetX: o,
      targetY: i,
      targetPosition: l
    }), _ = e.isInternal ? void 0 : t;
    return S.jsx(Vs, { id: _, path: g, labelX: h, labelY: v, label: u, labelStyle: a, labelShowBg: d, labelBgStyle: c, labelBgPadding: f, labelBgBorderRadius: m, style: y, markerEnd: w, markerStart: x, interactionWidth: p });
  });
}
const f2 = Jg({ isInternal: !1 }), em = Jg({ isInternal: !0 });
f2.displayName = "SimpleBezierEdge";
em.displayName = "SimpleBezierEdgeInternal";
function tm(e) {
  return j.memo(({ id: t, sourceX: n, sourceY: r, targetX: o, targetY: i, label: s, labelStyle: l, labelShowBg: u, labelBgStyle: a, labelBgPadding: d, labelBgBorderRadius: c, style: f, sourcePosition: m = Q.Bottom, targetPosition: y = Q.Top, markerEnd: w, markerStart: x, pathOptions: p, interactionWidth: g }) => {
    const [h, v, _] = Pu({
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
    return S.jsx(Vs, { id: E, path: h, labelX: v, labelY: _, label: s, labelStyle: l, labelShowBg: u, labelBgStyle: a, labelBgPadding: d, labelBgBorderRadius: c, style: f, markerEnd: w, markerStart: x, interactionWidth: g });
  });
}
const nm = tm({ isInternal: !1 }), rm = tm({ isInternal: !0 });
nm.displayName = "SmoothStepEdge";
rm.displayName = "SmoothStepEdgeInternal";
function om(e) {
  return j.memo(({ id: t, ...n }) => {
    var o;
    const r = e.isInternal ? void 0 : t;
    return S.jsx(nm, { ...n, id: r, pathOptions: j.useMemo(() => {
      var i;
      return { borderRadius: 0, offset: (i = n.pathOptions) == null ? void 0 : i.offset };
    }, [(o = n.pathOptions) == null ? void 0 : o.offset]) });
  });
}
const d2 = om({ isInternal: !1 }), im = om({ isInternal: !0 });
d2.displayName = "StepEdge";
im.displayName = "StepEdgeInternal";
function sm(e) {
  return j.memo(({ id: t, sourceX: n, sourceY: r, targetX: o, targetY: i, label: s, labelStyle: l, labelShowBg: u, labelBgStyle: a, labelBgPadding: d, labelBgBorderRadius: c, style: f, markerEnd: m, markerStart: y, interactionWidth: w }) => {
    const [x, p, g] = Sg({ sourceX: n, sourceY: r, targetX: o, targetY: i }), h = e.isInternal ? void 0 : t;
    return S.jsx(Vs, { id: h, path: x, labelX: p, labelY: g, label: s, labelStyle: l, labelShowBg: u, labelBgStyle: a, labelBgPadding: d, labelBgBorderRadius: c, style: f, markerEnd: m, markerStart: y, interactionWidth: w });
  });
}
const h2 = sm({ isInternal: !1 }), lm = sm({ isInternal: !0 });
h2.displayName = "StraightEdge";
lm.displayName = "StraightEdgeInternal";
function um(e) {
  return j.memo(({ id: t, sourceX: n, sourceY: r, targetX: o, targetY: i, sourcePosition: s = Q.Bottom, targetPosition: l = Q.Top, label: u, labelStyle: a, labelShowBg: d, labelBgStyle: c, labelBgPadding: f, labelBgBorderRadius: m, style: y, markerEnd: w, markerStart: x, pathOptions: p, interactionWidth: g }) => {
    const [h, v, _] = wg({
      sourceX: n,
      sourceY: r,
      sourcePosition: s,
      targetX: o,
      targetY: i,
      targetPosition: l,
      curvature: p == null ? void 0 : p.curvature
    }), E = e.isInternal ? void 0 : t;
    return S.jsx(Vs, { id: E, path: h, labelX: v, labelY: _, label: u, labelStyle: a, labelShowBg: d, labelBgStyle: c, labelBgPadding: f, labelBgBorderRadius: m, style: y, markerEnd: w, markerStart: x, interactionWidth: g });
  });
}
const p2 = um({ isInternal: !1 }), am = um({ isInternal: !0 });
p2.displayName = "BezierEdge";
am.displayName = "BezierEdgeInternal";
const ld = {
  default: am,
  straight: lm,
  step: im,
  smoothstep: rm,
  simplebezier: em
}, ud = {
  sourceX: null,
  sourceY: null,
  targetX: null,
  targetY: null,
  sourcePosition: null,
  targetPosition: null
}, g2 = (e, t, n) => n === Q.Left ? e - t : n === Q.Right ? e + t : e, m2 = (e, t, n) => n === Q.Top ? e - t : n === Q.Bottom ? e + t : e, ad = "react-flow__edgeupdater";
function cd({ position: e, centerX: t, centerY: n, radius: r = 10, onMouseDown: o, onMouseEnter: i, onMouseOut: s, type: l }) {
  return S.jsx("circle", { onMouseDown: o, onMouseEnter: i, onMouseOut: s, className: xe([ad, `${ad}-${l}`]), cx: g2(t, r, e), cy: m2(n, r, e), r, stroke: "transparent", fill: "transparent" });
}
function y2({ isReconnectable: e, reconnectRadius: t, edge: n, sourceX: r, sourceY: o, targetX: i, targetY: s, sourcePosition: l, targetPosition: u, onReconnect: a, onReconnectStart: d, onReconnectEnd: c, setReconnecting: f, setUpdateHover: m }) {
  const y = he(), w = (v, _) => {
    if (v.button !== 0)
      return;
    const { autoPanOnConnect: E, domNode: M, connectionMode: P, connectionRadius: T, lib: O, onConnectStart: z, cancelConnection: D, nodeLookup: H, rfId: k, panBy: A, updateConnection: I } = y.getState(), $ = _.type === "target", N = (F, R) => {
      f(!1), c == null || c(F, n, _.type, R);
    }, C = (F) => a == null ? void 0 : a(n, F), L = (F, R) => {
      f(!0), d == null || d(v, n, _.type), z == null || z(F, R);
    };
    Tu.onPointerDown(v.nativeEvent, {
      autoPanOnConnect: E,
      connectionMode: P,
      connectionRadius: T,
      domNode: M,
      handleId: _.id,
      nodeId: _.nodeId,
      nodeLookup: H,
      isTarget: $,
      edgeUpdaterType: _.type,
      lib: O,
      flowId: k,
      cancelConnection: D,
      panBy: A,
      isValidConnection: (...F) => {
        var R, U;
        return ((U = (R = y.getState()).isValidConnection) == null ? void 0 : U.call(R, ...F)) ?? !0;
      },
      onConnect: C,
      onConnectStart: L,
      onConnectEnd: (...F) => {
        var R, U;
        return (U = (R = y.getState()).onConnectEnd) == null ? void 0 : U.call(R, ...F);
      },
      onReconnectEnd: N,
      updateConnection: I,
      getTransform: () => y.getState().transform,
      getFromHandle: () => y.getState().connection.fromHandle,
      dragThreshold: y.getState().connectionDragThreshold,
      handleDomNode: v.currentTarget
    });
  }, x = (v) => w(v, { nodeId: n.target, id: n.targetHandle ?? null, type: "target" }), p = (v) => w(v, { nodeId: n.source, id: n.sourceHandle ?? null, type: "source" }), g = () => m(!0), h = () => m(!1);
  return S.jsxs(S.Fragment, { children: [(e === !0 || e === "source") && S.jsx(cd, { position: l, centerX: r, centerY: o, radius: t, onMouseDown: x, onMouseEnter: g, onMouseOut: h, type: "source" }), (e === !0 || e === "target") && S.jsx(cd, { position: u, centerX: i, centerY: s, radius: t, onMouseDown: p, onMouseEnter: g, onMouseOut: h, type: "target" })] });
}
function v2({ id: e, edgesFocusable: t, edgesReconnectable: n, elementsSelectable: r, onClick: o, onDoubleClick: i, onContextMenu: s, onMouseEnter: l, onMouseMove: u, onMouseLeave: a, reconnectRadius: d, onReconnect: c, onReconnectStart: f, onReconnectEnd: m, rfId: y, edgeTypes: w, noPanClassName: x, onError: p, disableKeyboardA11y: g }) {
  let h = ne((q) => q.edgeLookup.get(e));
  const v = ne((q) => q.defaultEdgeOptions);
  h = v ? { ...v, ...h } : h;
  let _ = h.type || "default", E = (w == null ? void 0 : w[_]) || ld[_];
  E === void 0 && (p == null || p("011", _t.error011(_)), _ = "default", E = (w == null ? void 0 : w.default) || ld.default);
  const M = !!(h.focusable || t && typeof h.focusable > "u"), P = typeof c < "u" && (h.reconnectable || n && typeof h.reconnectable > "u"), T = !!(h.selectable || r && typeof h.selectable > "u"), O = j.useRef(null), [z, D] = j.useState(!1), [H, k] = j.useState(!1), A = he(), { zIndex: I, sourceX: $, sourceY: N, targetX: C, targetY: L, sourcePosition: F, targetPosition: R } = ne(j.useCallback((q) => {
    const Z = q.nodeLookup.get(h.source), ie = q.nodeLookup.get(h.target);
    if (!Z || !ie)
      return {
        zIndex: h.zIndex,
        ...ud
      };
    const ue = QS({
      id: e,
      sourceNode: Z,
      targetNode: ie,
      sourceHandle: h.sourceHandle || null,
      targetHandle: h.targetHandle || null,
      connectionMode: q.connectionMode,
      onError: p
    });
    return {
      zIndex: HS({
        selected: h.selected,
        zIndex: h.zIndex,
        sourceNode: Z,
        targetNode: ie,
        elevateOnSelect: q.elevateEdgesOnSelect,
        zIndexMode: q.zIndexMode
      }),
      ...ue || ud
    };
  }, [h.source, h.target, h.sourceHandle, h.targetHandle, h.selected, h.zIndex]), de), U = j.useMemo(() => h.markerStart ? `url('#${Iu(h.markerStart, y)}')` : void 0, [h.markerStart, y]), W = j.useMemo(() => h.markerEnd ? `url('#${Iu(h.markerEnd, y)}')` : void 0, [h.markerEnd, y]);
  if (h.hidden || $ === null || N === null || C === null || L === null)
    return null;
  const X = (q) => {
    var oe;
    const { addSelectedEdges: Z, unselectNodesAndEdges: ie, multiSelectionActive: ue } = A.getState();
    T && (A.setState({ nodesSelectionActive: !1 }), h.selected && ue ? (ie({ nodes: [], edges: [h] }), (oe = O.current) == null || oe.blur()) : Z([e])), o && o(q, h);
  }, b = i ? (q) => {
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
    if (!g && og.includes(q.key) && T) {
      const { unselectNodesAndEdges: ie, addSelectedEdges: ue } = A.getState();
      q.key === "Escape" ? ((Z = O.current) == null || Z.blur(), ie({ edges: [h] })) : ue([e]);
    }
  };
  return S.jsx("svg", { style: { zIndex: I }, children: S.jsxs("g", { className: xe([
    "react-flow__edge",
    `react-flow__edge-${_}`,
    h.className,
    x,
    {
      selected: h.selected,
      animated: h.animated,
      inactive: !T && !o,
      updating: z,
      selectable: T
    }
  ]), onClick: X, onDoubleClick: b, onContextMenu: K, onMouseEnter: B, onMouseMove: G, onMouseLeave: ee, onKeyDown: M ? J : void 0, tabIndex: M ? 0 : void 0, role: h.ariaRole ?? (M ? "group" : "img"), "aria-roledescription": "edge", "data-id": e, "data-testid": `rf__edge-${e}`, "aria-label": h.ariaLabel === null ? void 0 : h.ariaLabel || `Edge from ${h.source} to ${h.target}`, "aria-describedby": M ? `${Hg}-${y}` : void 0, ref: O, ...h.domAttributes, children: [!H && S.jsx(E, { id: e, source: h.source, target: h.target, type: h.type, selected: h.selected, animated: h.animated, selectable: T, deletable: h.deletable ?? !0, label: h.label, labelStyle: h.labelStyle, labelShowBg: h.labelShowBg, labelBgStyle: h.labelBgStyle, labelBgPadding: h.labelBgPadding, labelBgBorderRadius: h.labelBgBorderRadius, sourceX: $, sourceY: N, targetX: C, targetY: L, sourcePosition: F, targetPosition: R, data: h.data, style: h.style, sourceHandleId: h.sourceHandle, targetHandleId: h.targetHandle, markerStart: U, markerEnd: W, pathOptions: "pathOptions" in h ? h.pathOptions : void 0, interactionWidth: h.interactionWidth }), P && S.jsx(y2, { edge: h, isReconnectable: P, reconnectRadius: d, onReconnect: c, onReconnectStart: f, onReconnectEnd: m, sourceX: $, sourceY: N, targetX: C, targetY: L, sourcePosition: F, targetPosition: R, setUpdateHover: D, setReconnecting: k })] }) });
}
var w2 = j.memo(v2);
const x2 = (e) => ({
  edgesFocusable: e.edgesFocusable,
  edgesReconnectable: e.edgesReconnectable,
  elementsSelectable: e.elementsSelectable,
  connectionMode: e.connectionMode,
  onError: e.onError
});
function cm({ defaultMarkerColor: e, onlyRenderVisibleElements: t, rfId: n, edgeTypes: r, noPanClassName: o, onReconnect: i, onEdgeContextMenu: s, onEdgeMouseEnter: l, onEdgeMouseMove: u, onEdgeMouseLeave: a, onEdgeClick: d, reconnectRadius: c, onEdgeDoubleClick: f, onReconnectStart: m, onReconnectEnd: y, disableKeyboardA11y: w }) {
  const { edgesFocusable: x, edgesReconnectable: p, elementsSelectable: g, onError: h } = ne(x2, de), v = o2(t);
  return S.jsxs("div", { className: "react-flow__edges", children: [S.jsx(a2, { defaultColor: e, rfId: n }), v.map((_) => S.jsx(w2, { id: _, edgesFocusable: x, edgesReconnectable: p, elementsSelectable: g, noPanClassName: o, onReconnect: i, onContextMenu: s, onMouseEnter: l, onMouseMove: u, onMouseLeave: a, onClick: d, reconnectRadius: c, onDoubleClick: f, onReconnectStart: m, onReconnectEnd: y, rfId: n, onError: h, edgeTypes: r, disableKeyboardA11y: w }, _))] });
}
cm.displayName = "EdgeRenderer";
const S2 = j.memo(cm), _2 = (e) => `translate(${e.transform[0]}px,${e.transform[1]}px) scale(${e.transform[2]})`;
function E2({ children: e }) {
  const t = ne(_2);
  return S.jsx("div", { className: "react-flow__viewport xyflow__viewport react-flow__container", style: { transform: t }, children: e });
}
function k2(e) {
  const t = Fs(), n = j.useRef(!1);
  j.useEffect(() => {
    !n.current && t.viewportInitialized && e && (setTimeout(() => e(t), 1), n.current = !0);
  }, [e, t.viewportInitialized]);
}
const N2 = (e) => {
  var t;
  return (t = e.panZoom) == null ? void 0 : t.syncViewport;
};
function C2(e) {
  const t = ne(N2), n = he();
  return j.useEffect(() => {
    e && (t == null || t(e), n.setState({ transform: [e.x, e.y, e.zoom] }));
  }, [e, t]), null;
}
function M2(e) {
  return e.connection.inProgress ? { ...e.connection, to: Fo(e.connection.to, e.transform) } : { ...e.connection };
}
function P2(e) {
  return M2;
}
function I2(e) {
  const t = P2();
  return ne(t, de);
}
const z2 = (e) => ({
  nodesConnectable: e.nodesConnectable,
  isValid: e.connection.isValid,
  inProgress: e.connection.inProgress,
  width: e.width,
  height: e.height
});
function T2({ containerStyle: e, style: t, type: n, component: r }) {
  const { nodesConnectable: o, width: i, height: s, isValid: l, inProgress: u } = ne(z2, de);
  return !(i && o && u) ? null : S.jsx("svg", { style: e, width: i, height: s, className: "react-flow__connectionline react-flow__container", children: S.jsx("g", { className: xe(["react-flow__connection", lg(l)]), children: S.jsx(fm, { style: t, type: n, CustomComponent: r, isValid: l }) }) });
}
const fm = ({ style: e, type: t = bt.Bezier, CustomComponent: n, isValid: r }) => {
  const { inProgress: o, from: i, fromNode: s, fromHandle: l, fromPosition: u, to: a, toNode: d, toHandle: c, toPosition: f, pointer: m } = I2();
  if (!o)
    return;
  if (n)
    return S.jsx(n, { connectionLineType: t, connectionLineStyle: e, fromNode: s, fromHandle: l, fromX: i.x, fromY: i.y, toX: a.x, toY: a.y, fromPosition: u, toPosition: f, connectionStatus: lg(r), toNode: d, toHandle: c, pointer: m });
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
    case bt.Bezier:
      [y] = wg(w);
      break;
    case bt.SimpleBezier:
      [y] = qg(w);
      break;
    case bt.Step:
      [y] = Pu({
        ...w,
        borderRadius: 0
      });
      break;
    case bt.SmoothStep:
      [y] = Pu(w);
      break;
    default:
      [y] = Sg(w);
  }
  return S.jsx("path", { d: y, fill: "none", className: "react-flow__connection-path", style: e });
};
fm.displayName = "ConnectionLine";
const L2 = {};
function fd(e = L2) {
  j.useRef(e), he(), j.useEffect(() => {
  }, [e]);
}
function A2() {
  he(), j.useRef(!1), j.useEffect(() => {
  }, []);
}
function dm({ nodeTypes: e, edgeTypes: t, onInit: n, onNodeClick: r, onEdgeClick: o, onNodeDoubleClick: i, onEdgeDoubleClick: s, onNodeMouseEnter: l, onNodeMouseMove: u, onNodeMouseLeave: a, onNodeContextMenu: d, onSelectionContextMenu: c, onSelectionStart: f, onSelectionEnd: m, connectionLineType: y, connectionLineStyle: w, connectionLineComponent: x, connectionLineContainerStyle: p, selectionKeyCode: g, selectionOnDrag: h, selectionMode: v, multiSelectionKeyCode: _, panActivationKeyCode: E, zoomActivationKeyCode: M, deleteKeyCode: P, onlyRenderVisibleElements: T, elementsSelectable: O, defaultViewport: z, translateExtent: D, minZoom: H, maxZoom: k, preventScrolling: A, defaultMarkerColor: I, zoomOnScroll: $, zoomOnPinch: N, panOnScroll: C, panOnScrollSpeed: L, panOnScrollMode: F, zoomOnDoubleClick: R, panOnDrag: U, onPaneClick: W, onPaneMouseEnter: X, onPaneMouseMove: b, onPaneMouseLeave: K, onPaneScroll: B, onPaneContextMenu: G, paneClickDistance: ee, nodeClickDistance: J, onEdgeContextMenu: q, onEdgeMouseEnter: Z, onEdgeMouseMove: ie, onEdgeMouseLeave: ue, reconnectRadius: oe, onReconnect: Pe, onReconnectStart: Ft, onReconnectEnd: kt, noDragClassName: fn, noWheelClassName: kr, noPanClassName: Nr, disableKeyboardA11y: Cr, nodeExtent: Bs, rfId: Ho, viewport: $n, onViewportChange: Mr }) {
  return fd(e), fd(t), A2(), k2(n), C2($n), S.jsx(QE, { onPaneClick: W, onPaneMouseEnter: X, onPaneMouseMove: b, onPaneMouseLeave: K, onPaneContextMenu: G, onPaneScroll: B, paneClickDistance: ee, deleteKeyCode: P, selectionKeyCode: g, selectionOnDrag: h, selectionMode: v, onSelectionStart: f, onSelectionEnd: m, multiSelectionKeyCode: _, panActivationKeyCode: E, zoomActivationKeyCode: M, elementsSelectable: O, zoomOnScroll: $, zoomOnPinch: N, zoomOnDoubleClick: R, panOnScroll: C, panOnScrollSpeed: L, panOnScrollMode: F, panOnDrag: U, defaultViewport: z, translateExtent: D, minZoom: H, maxZoom: k, onSelectionContextMenu: c, preventScrolling: A, noDragClassName: fn, noWheelClassName: kr, noPanClassName: Nr, disableKeyboardA11y: Cr, onViewportChange: Mr, isControlledViewport: !!$n, children: S.jsxs(E2, { children: [S.jsx(S2, { edgeTypes: t, onEdgeClick: o, onEdgeDoubleClick: s, onReconnect: Pe, onReconnectStart: Ft, onReconnectEnd: kt, onlyRenderVisibleElements: T, onEdgeContextMenu: q, onEdgeMouseEnter: Z, onEdgeMouseMove: ie, onEdgeMouseLeave: ue, reconnectRadius: oe, defaultMarkerColor: I, noPanClassName: Nr, disableKeyboardA11y: Cr, rfId: Ho }), S.jsx(T2, { style: w, type: y, component: x, containerStyle: p }), S.jsx("div", { className: "react-flow__edgelabel-renderer" }), S.jsx(r2, { nodeTypes: e, onNodeClick: r, onNodeDoubleClick: i, onNodeMouseEnter: l, onNodeMouseMove: u, onNodeMouseLeave: a, onNodeContextMenu: d, nodeClickDistance: J, onlyRenderVisibleElements: T, noPanClassName: Nr, noDragClassName: fn, disableKeyboardA11y: Cr, nodeExtent: Bs, rfId: Ho }), S.jsx("div", { className: "react-flow__viewport-portal" })] }) });
}
dm.displayName = "GraphView";
const $2 = j.memo(dm), dd = ({ nodes: e, edges: t, defaultNodes: n, defaultEdges: r, width: o, height: i, fitView: s, fitViewOptions: l, minZoom: u = 0.5, maxZoom: a = 2, nodeOrigin: d, nodeExtent: c, zIndexMode: f = "basic" } = {}) => {
  const m = /* @__PURE__ */ new Map(), y = /* @__PURE__ */ new Map(), w = /* @__PURE__ */ new Map(), x = /* @__PURE__ */ new Map(), p = r ?? t ?? [], g = n ?? e ?? [], h = d ?? [0, 0], v = c ?? Eo;
  kg(w, x, p);
  const { nodesInitialized: _ } = zu(g, m, y, {
    nodeOrigin: h,
    nodeExtent: v,
    zIndexMode: f
  });
  let E = [0, 0, 1];
  if (s && o && i) {
    const M = jo(m, {
      filter: (z) => !!((z.width || z.initialWidth) && (z.height || z.initialHeight))
    }), { x: P, y: T, zoom: O } = Oa(M, o, i, u, a, (l == null ? void 0 : l.padding) ?? 0.1);
    E = [P, T, O];
  }
  return {
    rfId: "1",
    width: o ?? 0,
    height: i ?? 0,
    transform: E,
    nodes: g,
    nodesInitialized: _,
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
    connection: { ...sg },
    connectionClickStartHandle: null,
    connectOnClick: !0,
    ariaLiveMessage: "",
    autoPanOnConnect: !0,
    autoPanOnNodeDrag: !0,
    autoPanOnNodeFocus: !0,
    autoPanSpeed: 15,
    connectionRadius: 20,
    onError: $S,
    isValidConnection: void 0,
    onSelectionChangeHandlers: [],
    lib: "react",
    debug: !1,
    ariaLabelConfig: ig,
    zIndexMode: f,
    onNodesChangeMiddlewareMap: /* @__PURE__ */ new Map(),
    onEdgesChangeMiddlewareMap: /* @__PURE__ */ new Map()
  };
}, D2 = ({ nodes: e, edges: t, defaultNodes: n, defaultEdges: r, width: o, height: i, fitView: s, fitViewOptions: l, minZoom: u, maxZoom: a, nodeOrigin: d, nodeExtent: c, zIndexMode: f }) => q_((m, y) => {
  async function w() {
    const { nodeLookup: x, panZoom: p, fitViewOptions: g, fitViewResolver: h, width: v, height: _, minZoom: E, maxZoom: M } = y();
    p && (await LS({
      nodes: x,
      width: v,
      height: _,
      panZoom: p,
      minZoom: E,
      maxZoom: M
    }, g), h == null || h.resolve(!0), m({ fitViewResolver: null }));
  }
  return {
    ...dd({
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
      const { nodeLookup: p, parentLookup: g, nodeOrigin: h, elevateNodesOnSelect: v, fitViewQueued: _, zIndexMode: E, nodesSelectionActive: M } = y(), { nodesInitialized: P, hasSelectedNodes: T } = zu(x, p, g, {
        nodeOrigin: h,
        nodeExtent: c,
        elevateNodesOnSelect: v,
        checkEquality: !0,
        zIndexMode: E
      }), O = M && T;
      _ && P ? (w(), m({
        nodes: x,
        nodesInitialized: P,
        fitViewQueued: !1,
        fitViewOptions: void 0,
        nodesSelectionActive: O
      })) : m({ nodes: x, nodesInitialized: P, nodesSelectionActive: O });
    },
    setEdges: (x) => {
      const { connectionLookup: p, edgeLookup: g } = y();
      kg(p, g, x), m({ edges: x });
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
      const { triggerNodeChanges: p, nodeLookup: g, parentLookup: h, domNode: v, nodeOrigin: _, nodeExtent: E, debug: M, fitViewQueued: P, zIndexMode: T } = y(), { changes: O, updatedInternals: z } = n_(x, g, h, v, _, E, T);
      z && (qS(g, h, { nodeOrigin: _, nodeExtent: E, zIndexMode: T }), P ? (w(), m({ fitViewQueued: !1, fitViewOptions: void 0 })) : m({}), (O == null ? void 0 : O.length) > 0 && (M && console.log("React Flow: trigger node changes", O), p == null || p(O)));
    },
    updateNodePositions: (x, p = !1) => {
      const g = [];
      let h = [];
      const { nodeLookup: v, triggerNodeChanges: _, connection: E, updateConnection: M, onNodesChangeMiddlewareMap: P } = y();
      for (const [T, O] of x) {
        const z = v.get(T), D = !!(z != null && z.expandParent && (z != null && z.parentId) && (O != null && O.position)), H = {
          id: T,
          type: "position",
          position: D ? {
            x: Math.max(0, O.position.x),
            y: Math.max(0, O.position.y)
          } : O.position,
          dragging: p
        };
        if (z && E.inProgress && E.fromNode.id === z.id) {
          const k = Tn(z, E.fromHandle, Q.Left, !0);
          M({ ...E, from: k });
        }
        D && z.parentId && g.push({
          id: T,
          parentId: z.parentId,
          rect: {
            ...O.internals.positionAbsolute,
            width: O.measured.width ?? 0,
            height: O.measured.height ?? 0
          }
        }), h.push(H);
      }
      if (g.length > 0) {
        const { parentLookup: T, nodeOrigin: O } = y(), z = Ua(g, v, T, O);
        h.push(...z);
      }
      for (const T of P.values())
        h = T(h);
      _(h);
    },
    triggerNodeChanges: (x) => {
      const { onNodesChange: p, setNodes: g, nodes: h, hasDefaultNodes: v, debug: _ } = y();
      if (x != null && x.length) {
        if (v) {
          const E = wE(x, h);
          g(E);
        }
        _ && console.log("React Flow: trigger node changes", x), p == null || p(x);
      }
    },
    triggerEdgeChanges: (x) => {
      const { onEdgesChange: p, setEdges: g, edges: h, hasDefaultEdges: v, debug: _ } = y();
      if (x != null && x.length) {
        if (v) {
          const E = xE(x, h);
          g(E);
        }
        _ && console.log("React Flow: trigger edge changes", x), p == null || p(x);
      }
    },
    addSelectedNodes: (x) => {
      const { multiSelectionActive: p, edgeLookup: g, nodeLookup: h, triggerNodeChanges: v, triggerEdgeChanges: _ } = y();
      if (p) {
        const E = x.map((M) => pn(M, !0));
        v(E);
        return;
      }
      v(Gn(h, /* @__PURE__ */ new Set([...x]), !0)), _(Gn(g));
    },
    addSelectedEdges: (x) => {
      const { multiSelectionActive: p, edgeLookup: g, nodeLookup: h, triggerNodeChanges: v, triggerEdgeChanges: _ } = y();
      if (p) {
        const E = x.map((M) => pn(M, !0));
        _(E);
        return;
      }
      _(Gn(g, /* @__PURE__ */ new Set([...x]))), v(Gn(h, /* @__PURE__ */ new Set(), !0));
    },
    unselectNodesAndEdges: ({ nodes: x, edges: p } = {}) => {
      const { edges: g, nodes: h, nodeLookup: v, triggerNodeChanges: _, triggerEdgeChanges: E } = y(), M = x || h, P = p || g, T = [];
      for (const z of M) {
        if (!z.selected)
          continue;
        const D = v.get(z.id);
        D && (D.selected = !1), T.push(pn(z.id, !1));
      }
      const O = [];
      for (const z of P)
        z.selected && O.push(pn(z.id, !1));
      _(T), E(O);
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
      const _ = p.reduce((M, P) => P.selected ? [...M, pn(P.id, !1)] : M, []), E = x.reduce((M, P) => P.selected ? [...M, pn(P.id, !1)] : M, []);
      g(_), h(E);
    },
    setNodeExtent: (x) => {
      const { nodes: p, nodeLookup: g, parentLookup: h, nodeOrigin: v, elevateNodesOnSelect: _, nodeExtent: E, zIndexMode: M } = y();
      x[0][0] === E[0][0] && x[0][1] === E[0][1] && x[1][0] === E[1][0] && x[1][1] === E[1][1] || (zu(p, g, h, {
        nodeOrigin: v,
        nodeExtent: x,
        elevateNodesOnSelect: _,
        checkEquality: !1,
        zIndexMode: M
      }), m({ nodeExtent: x }));
    },
    panBy: (x) => {
      const { transform: p, width: g, height: h, panZoom: v, translateExtent: _ } = y();
      return r_({ delta: x, panZoom: v, transform: p, translateExtent: _, width: g, height: h });
    },
    setCenter: async (x, p, g) => {
      const { width: h, height: v, maxZoom: _, panZoom: E } = y();
      if (!E)
        return Promise.resolve(!1);
      const M = typeof (g == null ? void 0 : g.zoom) < "u" ? g.zoom : _;
      return await E.setViewport({
        x: h / 2 - x * M,
        y: v / 2 - p * M,
        zoom: M
      }, { duration: g == null ? void 0 : g.duration, ease: g == null ? void 0 : g.ease, interpolate: g == null ? void 0 : g.interpolate }), Promise.resolve(!0);
    },
    cancelConnection: () => {
      m({
        connection: { ...sg }
      });
    },
    updateConnection: (x) => {
      m({ connection: x });
    },
    reset: () => m({ ...dd() })
  };
}, Object.is);
function hm({ initialNodes: e, initialEdges: t, defaultNodes: n, defaultEdges: r, initialWidth: o, initialHeight: i, initialMinZoom: s, initialMaxZoom: l, initialFitViewOptions: u, fitView: a, nodeOrigin: d, nodeExtent: c, zIndexMode: f, children: m }) {
  const [y] = j.useState(() => D2({
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
  return S.jsx(J_, { value: y, children: S.jsx(EE, { children: m }) });
}
function R2({ children: e, nodes: t, edges: n, defaultNodes: r, defaultEdges: o, width: i, height: s, fitView: l, fitViewOptions: u, minZoom: a, maxZoom: d, nodeOrigin: c, nodeExtent: f, zIndexMode: m }) {
  return j.useContext(js) ? S.jsx(S.Fragment, { children: e }) : S.jsx(hm, { initialNodes: t, initialEdges: n, defaultNodes: r, defaultEdges: o, initialWidth: i, initialHeight: s, fitView: l, initialFitViewOptions: u, initialMinZoom: a, initialMaxZoom: d, nodeOrigin: c, nodeExtent: f, zIndexMode: m, children: e });
}
const j2 = {
  width: "100%",
  height: "100%",
  overflow: "hidden",
  position: "relative",
  zIndex: 0
};
function O2({ nodes: e, edges: t, defaultNodes: n, defaultEdges: r, className: o, nodeTypes: i, edgeTypes: s, onNodeClick: l, onEdgeClick: u, onInit: a, onMove: d, onMoveStart: c, onMoveEnd: f, onConnect: m, onConnectStart: y, onConnectEnd: w, onClickConnectStart: x, onClickConnectEnd: p, onNodeMouseEnter: g, onNodeMouseMove: h, onNodeMouseLeave: v, onNodeContextMenu: _, onNodeDoubleClick: E, onNodeDragStart: M, onNodeDrag: P, onNodeDragStop: T, onNodesDelete: O, onEdgesDelete: z, onDelete: D, onSelectionChange: H, onSelectionDragStart: k, onSelectionDrag: A, onSelectionDragStop: I, onSelectionContextMenu: $, onSelectionStart: N, onSelectionEnd: C, onBeforeDelete: L, connectionMode: F, connectionLineType: R = bt.Bezier, connectionLineStyle: U, connectionLineComponent: W, connectionLineContainerStyle: X, deleteKeyCode: b = "Backspace", selectionKeyCode: K = "Shift", selectionOnDrag: B = !1, selectionMode: G = ko.Full, panActivationKeyCode: ee = "Space", multiSelectionKeyCode: J = Mo() ? "Meta" : "Control", zoomActivationKeyCode: q = Mo() ? "Meta" : "Control", snapToGrid: Z, snapGrid: ie, onlyRenderVisibleElements: ue = !1, selectNodesOnDrag: oe, nodesDraggable: Pe, autoPanOnNodeFocus: Ft, nodesConnectable: kt, nodesFocusable: fn, nodeOrigin: kr = Vg, edgesFocusable: Nr, edgesReconnectable: Cr, elementsSelectable: Bs = !0, defaultViewport: Ho = dE, minZoom: $n = 0.5, maxZoom: Mr = 2, translateExtent: ba = Eo, preventScrolling: wm = !0, nodeExtent: Ws, defaultMarkerColor: xm = "#b1b1b7", zoomOnScroll: Sm = !0, zoomOnPinch: _m = !0, panOnScroll: Em = !1, panOnScrollSpeed: km = 0.5, panOnScrollMode: Nm = _n.Free, zoomOnDoubleClick: Cm = !0, panOnDrag: Mm = !0, onPaneClick: Pm, onPaneMouseEnter: Im, onPaneMouseMove: zm, onPaneMouseLeave: Tm, onPaneScroll: Lm, onPaneContextMenu: Am, paneClickDistance: $m = 1, nodeClickDistance: Dm = 0, children: Rm, onReconnect: jm, onReconnectStart: Om, onReconnectEnd: Fm, onEdgeContextMenu: Hm, onEdgeDoubleClick: Vm, onEdgeMouseEnter: Bm, onEdgeMouseMove: Wm, onEdgeMouseLeave: Um, reconnectRadius: Ym = 10, onNodesChange: Xm, onEdgesChange: bm, noDragClassName: Qm = "nodrag", noWheelClassName: Km = "nowheel", noPanClassName: Qa = "nopan", fitView: Ka, fitViewOptions: Ga, connectOnClick: Gm, attributionPosition: Zm, proOptions: qm, defaultEdgeOptions: Jm, elevateNodesOnSelect: e0 = !0, elevateEdgesOnSelect: t0 = !1, disableKeyboardA11y: Za = !1, autoPanOnConnect: n0, autoPanOnNodeDrag: r0, autoPanSpeed: o0, connectionRadius: i0, isValidConnection: s0, onError: l0, style: u0, id: qa, nodeDragThreshold: a0, connectionDragThreshold: c0, viewport: f0, onViewportChange: d0, width: h0, height: p0, colorMode: g0 = "light", debug: m0, onScroll: Vo, ariaLabelConfig: y0, zIndexMode: Ja = "basic", ...v0 }, w0) {
  const Us = qa || "1", x0 = mE(g0), S0 = j.useCallback((ec) => {
    ec.currentTarget.scrollTo({ top: 0, left: 0, behavior: "instant" }), Vo == null || Vo(ec);
  }, [Vo]);
  return S.jsx("div", { "data-testid": "rf__wrapper", ...v0, onScroll: S0, style: { ...u0, ...j2 }, ref: w0, className: xe(["react-flow", o, x0]), id: qa, role: "application", children: S.jsxs(R2, { nodes: e, edges: t, width: h0, height: p0, fitView: Ka, fitViewOptions: Ga, minZoom: $n, maxZoom: Mr, nodeOrigin: kr, nodeExtent: Ws, zIndexMode: Ja, children: [S.jsx(gE, { nodes: e, edges: t, defaultNodes: n, defaultEdges: r, onConnect: m, onConnectStart: y, onConnectEnd: w, onClickConnectStart: x, onClickConnectEnd: p, nodesDraggable: Pe, autoPanOnNodeFocus: Ft, nodesConnectable: kt, nodesFocusable: fn, edgesFocusable: Nr, edgesReconnectable: Cr, elementsSelectable: Bs, elevateNodesOnSelect: e0, elevateEdgesOnSelect: t0, minZoom: $n, maxZoom: Mr, nodeExtent: Ws, onNodesChange: Xm, onEdgesChange: bm, snapToGrid: Z, snapGrid: ie, connectionMode: F, translateExtent: ba, connectOnClick: Gm, defaultEdgeOptions: Jm, fitView: Ka, fitViewOptions: Ga, onNodesDelete: O, onEdgesDelete: z, onDelete: D, onNodeDragStart: M, onNodeDrag: P, onNodeDragStop: T, onSelectionDrag: A, onSelectionDragStart: k, onSelectionDragStop: I, onMove: d, onMoveStart: c, onMoveEnd: f, noPanClassName: Qa, nodeOrigin: kr, rfId: Us, autoPanOnConnect: n0, autoPanOnNodeDrag: r0, autoPanSpeed: o0, onError: l0, connectionRadius: i0, isValidConnection: s0, selectNodesOnDrag: oe, nodeDragThreshold: a0, connectionDragThreshold: c0, onBeforeDelete: L, debug: m0, ariaLabelConfig: y0, zIndexMode: Ja }), S.jsx($2, { onInit: a, onNodeClick: l, onEdgeClick: u, onNodeMouseEnter: g, onNodeMouseMove: h, onNodeMouseLeave: v, onNodeContextMenu: _, onNodeDoubleClick: E, nodeTypes: i, edgeTypes: s, connectionLineType: R, connectionLineStyle: U, connectionLineComponent: W, connectionLineContainerStyle: X, selectionKeyCode: K, selectionOnDrag: B, selectionMode: G, deleteKeyCode: b, multiSelectionKeyCode: J, panActivationKeyCode: ee, zoomActivationKeyCode: q, onlyRenderVisibleElements: ue, defaultViewport: Ho, translateExtent: ba, minZoom: $n, maxZoom: Mr, preventScrolling: wm, zoomOnScroll: Sm, zoomOnPinch: _m, zoomOnDoubleClick: Cm, panOnScroll: Em, panOnScrollSpeed: km, panOnScrollMode: Nm, panOnDrag: Mm, onPaneClick: Pm, onPaneMouseEnter: Im, onPaneMouseMove: zm, onPaneMouseLeave: Tm, onPaneScroll: Lm, onPaneContextMenu: Am, paneClickDistance: $m, nodeClickDistance: Dm, onSelectionContextMenu: $, onSelectionStart: N, onSelectionEnd: C, onReconnect: jm, onReconnectStart: Om, onReconnectEnd: Fm, onEdgeContextMenu: Hm, onEdgeDoubleClick: Vm, onEdgeMouseEnter: Bm, onEdgeMouseMove: Wm, onEdgeMouseLeave: Um, reconnectRadius: Ym, defaultMarkerColor: xm, noDragClassName: Qm, noWheelClassName: Km, noPanClassName: Qa, rfId: Us, disableKeyboardA11y: Za, nodeExtent: Ws, viewport: f0, onViewportChange: d0 }), S.jsx(fE, { onSelectionChange: H }), Rm, S.jsx(sE, { proOptions: qm, position: Zm }), S.jsx(iE, { rfId: Us, disableKeyboardA11y: Za })] }) });
}
var F2 = Wg(O2);
function H2({ dimensions: e, lineWidth: t, variant: n, className: r }) {
  return S.jsx("path", { strokeWidth: t, d: `M${e[0] / 2} 0 V${e[1]} M0 ${e[1] / 2} H${e[0]}`, className: xe(["react-flow__background-pattern", n, r]) });
}
function V2({ radius: e, className: t }) {
  return S.jsx("circle", { cx: e, cy: e, r: e, className: xe(["react-flow__background-pattern", "dots", t]) });
}
var on;
(function(e) {
  e.Lines = "lines", e.Dots = "dots", e.Cross = "cross";
})(on || (on = {}));
const B2 = {
  [on.Dots]: 1,
  [on.Lines]: 1,
  [on.Cross]: 6
}, W2 = (e) => ({ transform: e.transform, patternId: `pattern-${e.rfId}` });
function pm({
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
  const c = j.useRef(null), { transform: f, patternId: m } = ne(W2, de), y = r || B2[t], w = t === on.Dots, x = t === on.Cross, p = Array.isArray(n) ? n : [n, n], g = [p[0] * f[2] || 1, p[1] * f[2] || 1], h = y * f[2], v = Array.isArray(i) ? i : [i, i], _ = x ? [h, h] : g, E = [
    v[0] * f[2] || 1 + _[0] / 2,
    v[1] * f[2] || 1 + _[1] / 2
  ], M = `${m}${e || ""}`;
  return S.jsxs("svg", { className: xe(["react-flow__background", a]), style: {
    ...u,
    ...Hs,
    "--xy-background-color-props": l,
    "--xy-background-pattern-color-props": s
  }, ref: c, "data-testid": "rf__background", children: [S.jsx("pattern", { id: M, x: f[0] % g[0], y: f[1] % g[1], width: g[0], height: g[1], patternUnits: "userSpaceOnUse", patternTransform: `translate(-${E[0]},-${E[1]})`, children: w ? S.jsx(V2, { radius: h / 2, className: d }) : S.jsx(H2, { dimensions: _, lineWidth: o, variant: t, className: d }) }), S.jsx("rect", { x: "0", y: "0", width: "100%", height: "100%", fill: `url(#${M})` })] });
}
pm.displayName = "Background";
const U2 = j.memo(pm);
function Y2() {
  return S.jsx("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 32 32", children: S.jsx("path", { d: "M32 18.133H18.133V32h-4.266V18.133H0v-4.266h13.867V0h4.266v13.867H32z" }) });
}
function X2() {
  return S.jsx("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 32 5", children: S.jsx("path", { d: "M0 0h32v4.2H0z" }) });
}
function b2() {
  return S.jsx("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 32 30", children: S.jsx("path", { d: "M3.692 4.63c0-.53.4-.938.939-.938h5.215V0H4.708C2.13 0 0 2.054 0 4.63v5.216h3.692V4.631zM27.354 0h-5.2v3.692h5.17c.53 0 .984.4.984.939v5.215H32V4.631A4.624 4.624 0 0027.354 0zm.954 24.83c0 .532-.4.94-.939.94h-5.215v3.768h5.215c2.577 0 4.631-2.13 4.631-4.707v-5.139h-3.692v5.139zm-23.677.94c-.531 0-.939-.4-.939-.94v-5.138H0v5.139c0 2.577 2.13 4.707 4.708 4.707h5.138V25.77H4.631z" }) });
}
function Q2() {
  return S.jsx("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 25 32", children: S.jsx("path", { d: "M21.333 10.667H19.81V7.619C19.81 3.429 16.38 0 12.19 0 8 0 4.571 3.429 4.571 7.619v3.048H3.048A3.056 3.056 0 000 13.714v15.238A3.056 3.056 0 003.048 32h18.285a3.056 3.056 0 003.048-3.048V13.714a3.056 3.056 0 00-3.048-3.047zM12.19 24.533a3.056 3.056 0 01-3.047-3.047 3.056 3.056 0 013.047-3.048 3.056 3.056 0 013.048 3.048 3.056 3.056 0 01-3.048 3.047zm4.724-13.866H7.467V7.619c0-2.59 2.133-4.724 4.723-4.724 2.591 0 4.724 2.133 4.724 4.724v3.048z" }) });
}
function K2() {
  return S.jsx("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 25 32", children: S.jsx("path", { d: "M21.333 10.667H19.81V7.619C19.81 3.429 16.38 0 12.19 0c-4.114 1.828-1.37 2.133.305 2.438 1.676.305 4.42 2.59 4.42 5.181v3.048H3.047A3.056 3.056 0 000 13.714v15.238A3.056 3.056 0 003.048 32h18.285a3.056 3.056 0 003.048-3.048V13.714a3.056 3.056 0 00-3.048-3.047zM12.19 24.533a3.056 3.056 0 01-3.047-3.047 3.056 3.056 0 013.047-3.048 3.056 3.056 0 013.048 3.048 3.056 3.056 0 01-3.048 3.047z" }) });
}
function hi({ children: e, className: t, ...n }) {
  return S.jsx("button", { type: "button", className: xe(["react-flow__controls-button", t]), ...n, children: e });
}
const G2 = (e) => ({
  isInteractive: e.nodesDraggable || e.nodesConnectable || e.elementsSelectable,
  minZoomReached: e.transform[2] <= e.minZoom,
  maxZoomReached: e.transform[2] >= e.maxZoom,
  ariaLabelConfig: e.ariaLabelConfig
});
function gm({ style: e, showZoom: t = !0, showFitView: n = !0, showInteractive: r = !0, fitViewOptions: o, onZoomIn: i, onZoomOut: s, onFitView: l, onInteractiveChange: u, className: a, children: d, position: c = "bottom-left", orientation: f = "vertical", "aria-label": m }) {
  const y = he(), { isInteractive: w, minZoomReached: x, maxZoomReached: p, ariaLabelConfig: g } = ne(G2, de), { zoomIn: h, zoomOut: v, fitView: _ } = Fs(), E = () => {
    h(), i == null || i();
  }, M = () => {
    v(), s == null || s();
  }, P = () => {
    _(o), l == null || l();
  }, T = () => {
    y.setState({
      nodesDraggable: !w,
      nodesConnectable: !w,
      elementsSelectable: !w
    }), u == null || u(!w);
  }, O = f === "horizontal" ? "horizontal" : "vertical";
  return S.jsxs(Os, { className: xe(["react-flow__controls", O, a]), position: c, style: e, "data-testid": "rf__controls", "aria-label": m ?? g["controls.ariaLabel"], children: [t && S.jsxs(S.Fragment, { children: [S.jsx(hi, { onClick: E, className: "react-flow__controls-zoomin", title: g["controls.zoomIn.ariaLabel"], "aria-label": g["controls.zoomIn.ariaLabel"], disabled: p, children: S.jsx(Y2, {}) }), S.jsx(hi, { onClick: M, className: "react-flow__controls-zoomout", title: g["controls.zoomOut.ariaLabel"], "aria-label": g["controls.zoomOut.ariaLabel"], disabled: x, children: S.jsx(X2, {}) })] }), n && S.jsx(hi, { className: "react-flow__controls-fitview", onClick: P, title: g["controls.fitView.ariaLabel"], "aria-label": g["controls.fitView.ariaLabel"], children: S.jsx(b2, {}) }), r && S.jsx(hi, { className: "react-flow__controls-interactive", onClick: T, title: g["controls.interactive.ariaLabel"], "aria-label": g["controls.interactive.ariaLabel"], children: w ? S.jsx(K2, {}) : S.jsx(Q2, {}) }), d] });
}
gm.displayName = "Controls";
const Z2 = j.memo(gm);
function q2({ id: e, x: t, y: n, width: r, height: o, style: i, color: s, strokeColor: l, strokeWidth: u, className: a, borderRadius: d, shapeRendering: c, selected: f, onClick: m }) {
  const { background: y, backgroundColor: w } = i || {}, x = s || y || w;
  return S.jsx("rect", { className: xe(["react-flow__minimap-node", { selected: f }, a]), x: t, y: n, rx: d, ry: d, width: r, height: o, style: {
    fill: x,
    stroke: l,
    strokeWidth: u
  }, shapeRendering: c, onClick: m ? (p) => m(p, e) : void 0 });
}
const J2 = j.memo(q2), ek = (e) => e.nodes.map((t) => t.id), Cl = (e) => e instanceof Function ? e : () => e;
function tk({
  nodeStrokeColor: e,
  nodeColor: t,
  nodeClassName: n = "",
  nodeBorderRadius: r = 5,
  nodeStrokeWidth: o,
  /*
   * We need to rename the prop to be `CapitalCase` so that JSX will render it as
   * a component properly.
   */
  nodeComponent: i = J2,
  onClick: s
}) {
  const l = ne(ek, de), u = Cl(t), a = Cl(e), d = Cl(n), c = typeof window > "u" || window.chrome ? "crispEdges" : "geometricPrecision";
  return S.jsx(S.Fragment, { children: l.map((f) => (
    /*
     * The split of responsibilities between MiniMapNodes and
     * NodeComponentWrapper may appear weird. However, it’s designed to
     * minimize the cost of updates when individual nodes change.
     *
     * For more details, see a similar commit in `NodeRenderer/index.tsx`.
     */
    S.jsx(rk, { id: f, nodeColorFunc: u, nodeStrokeColorFunc: a, nodeClassNameFunc: d, nodeBorderRadius: r, nodeStrokeWidth: o, NodeComponent: i, onClick: s, shapeRendering: c }, f)
  )) });
}
function nk({ id: e, nodeColorFunc: t, nodeStrokeColorFunc: n, nodeClassNameFunc: r, nodeBorderRadius: o, nodeStrokeWidth: i, shapeRendering: s, NodeComponent: l, onClick: u }) {
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
  return !a || a.hidden || !hg(a) ? null : S.jsx(l, { x: d, y: c, width: f, height: m, style: a.style, selected: !!a.selected, className: r(a), color: t(a), borderRadius: o, strokeColor: n(a), strokeWidth: i, shapeRendering: s, onClick: u, id: a.id });
}
const rk = j.memo(nk);
var ok = j.memo(tk);
const ik = 200, sk = 150, lk = (e) => !e.hidden, uk = (e) => {
  const t = {
    x: -e.transform[0] / e.transform[2],
    y: -e.transform[1] / e.transform[2],
    width: e.width / e.transform[2],
    height: e.height / e.transform[2]
  };
  return {
    viewBB: t,
    boundingRect: e.nodeLookup.size > 0 ? dg(jo(e.nodeLookup, { filter: lk }), t) : t,
    rfId: e.rfId,
    panZoom: e.panZoom,
    translateExtent: e.translateExtent,
    flowWidth: e.width,
    flowHeight: e.height,
    ariaLabelConfig: e.ariaLabelConfig
  };
}, ak = "react-flow__minimap-desc";
function mm({
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
  const _ = he(), E = j.useRef(null), { boundingRect: M, viewBB: P, rfId: T, panZoom: O, translateExtent: z, flowWidth: D, flowHeight: H, ariaLabelConfig: k } = ne(uk, de), A = (e == null ? void 0 : e.width) ?? ik, I = (e == null ? void 0 : e.height) ?? sk, $ = M.width / A, N = M.height / I, C = Math.max($, N), L = C * A, F = C * I, R = v * C, U = M.x - (L - M.width) / 2 - R, W = M.y - (F - M.height) / 2 - R, X = L + R * 2, b = F + R * 2, K = `${ak}-${T}`, B = j.useRef(0), G = j.useRef();
  B.current = C, j.useEffect(() => {
    if (E.current && O)
      return G.current = d_({
        domNode: E.current,
        panZoom: O,
        getTransform: () => _.getState().transform,
        getViewScale: () => B.current
      }), () => {
        var Z;
        (Z = G.current) == null || Z.destroy();
      };
  }, [O]), j.useEffect(() => {
    var Z;
    (Z = G.current) == null || Z.update({
      translateExtent: z,
      width: D,
      height: H,
      inversePan: g,
      pannable: w,
      zoomStep: h,
      zoomable: x
    });
  }, [w, x, g, h, z, D, H]);
  const ee = m ? (Z) => {
    var oe;
    const [ie, ue] = ((oe = G.current) == null ? void 0 : oe.pointer(Z)) || [0, 0];
    m(Z, { x: ie, y: ue });
  } : void 0, J = y ? j.useCallback((Z, ie) => {
    const ue = _.getState().nodeLookup.get(ie).internals.userNode;
    y(Z, ue);
  }, []) : void 0, q = p ?? k["minimap.ariaLabel"];
  return S.jsx(Os, { position: f, style: {
    ...e,
    "--xy-minimap-background-color-props": typeof u == "string" ? u : void 0,
    "--xy-minimap-mask-background-color-props": typeof a == "string" ? a : void 0,
    "--xy-minimap-mask-stroke-color-props": typeof d == "string" ? d : void 0,
    "--xy-minimap-mask-stroke-width-props": typeof c == "number" ? c * C : void 0,
    "--xy-minimap-node-background-color-props": typeof r == "string" ? r : void 0,
    "--xy-minimap-node-stroke-color-props": typeof n == "string" ? n : void 0,
    "--xy-minimap-node-stroke-width-props": typeof s == "number" ? s : void 0
  }, className: xe(["react-flow__minimap", t]), "data-testid": "rf__minimap", children: S.jsxs("svg", { width: A, height: I, viewBox: `${U} ${W} ${X} ${b}`, className: "react-flow__minimap-svg", role: "img", "aria-labelledby": K, ref: E, onClick: ee, children: [q && S.jsx("title", { id: K, children: q }), S.jsx(ok, { onClick: J, nodeColor: r, nodeStrokeColor: n, nodeBorderRadius: i, nodeClassName: o, nodeStrokeWidth: s, nodeComponent: l }), S.jsx("path", { className: "react-flow__minimap-mask", d: `M${U - R},${W - R}h${X + R * 2}v${b + R * 2}h${-X - R * 2}z
        M${P.x},${P.y}h${P.width}v${P.height}h${-P.width}z`, fillRule: "evenodd", pointerEvents: "none" })] }) });
}
mm.displayName = "MiniMap";
j.memo(mm);
const ck = (e) => (t) => e ? `${Math.max(1 / t.transform[2], 1)}` : void 0, fk = {
  [vr.Line]: "right",
  [vr.Handle]: "bottom-right"
};
function dk({ nodeId: e, position: t, variant: n = vr.Handle, className: r, style: o = void 0, children: i, color: s, minWidth: l = 10, minHeight: u = 10, maxWidth: a = Number.MAX_VALUE, maxHeight: d = Number.MAX_VALUE, keepAspectRatio: c = !1, resizeDirection: f, autoScale: m = !0, shouldResize: y, onResizeStart: w, onResize: x, onResizeEnd: p }) {
  const g = bg(), h = typeof e == "string" ? e : g, v = he(), _ = j.useRef(null), E = n === vr.Handle, M = ne(j.useCallback(ck(E && m), [E, m]), de), P = j.useRef(null), T = t ?? fk[n];
  j.useEffect(() => {
    if (!(!_.current || !h))
      return P.current || (P.current = C_({
        domNode: _.current,
        nodeId: h,
        getStoreItems: () => {
          const { nodeLookup: z, transform: D, snapGrid: H, snapToGrid: k, nodeOrigin: A, domNode: I } = v.getState();
          return {
            nodeLookup: z,
            transform: D,
            snapGrid: H,
            snapToGrid: k,
            nodeOrigin: A,
            paneDomNode: I
          };
        },
        onChange: (z, D) => {
          const { triggerNodeChanges: H, nodeLookup: k, parentLookup: A, nodeOrigin: I } = v.getState(), $ = [], N = { x: z.x, y: z.y }, C = k.get(h);
          if (C && C.expandParent && C.parentId) {
            const L = C.origin ?? I, F = z.width ?? C.measured.width ?? 0, R = z.height ?? C.measured.height ?? 0, U = {
              id: C.id,
              parentId: C.parentId,
              rect: {
                width: F,
                height: R,
                ...pg({
                  x: z.x ?? C.position.x,
                  y: z.y ?? C.position.y
                }, { width: F, height: R }, C.parentId, k, L)
              }
            }, W = Ua([U], k, A, I);
            $.push(...W), N.x = z.x ? Math.max(L[0] * F, z.x) : void 0, N.y = z.y ? Math.max(L[1] * R, z.y) : void 0;
          }
          if (N.x !== void 0 && N.y !== void 0) {
            const L = {
              id: h,
              type: "position",
              position: { ...N }
            };
            $.push(L);
          }
          if (z.width !== void 0 && z.height !== void 0) {
            const F = {
              id: h,
              type: "dimensions",
              resizing: !0,
              setAttributes: f ? f === "horizontal" ? "width" : "height" : !0,
              dimensions: {
                width: z.width,
                height: z.height
              }
            };
            $.push(F);
          }
          for (const L of D) {
            const F = {
              ...L,
              type: "position"
            };
            $.push(F);
          }
          H($);
        },
        onEnd: ({ width: z, height: D }) => {
          const H = {
            id: h,
            type: "dimensions",
            resizing: !1,
            dimensions: {
              width: z,
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
        var z;
        (z = P.current) == null || z.destroy();
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
  const O = T.split("-");
  return S.jsx("div", { className: xe(["react-flow__resize-control", "nodrag", ...O, n, r]), ref: _, style: {
    ...o,
    scale: M,
    ...s && { [E ? "backgroundColor" : "borderColor"]: s }
  }, children: i });
}
j.memo(dk);
const hd = /* @__PURE__ */ new WeakMap(), hk = {
  teamOrgNode: mk
};
function pk({ payload: e }) {
  return e ? /* @__PURE__ */ S.jsx(hm, { children: /* @__PURE__ */ S.jsx(gk, { payload: e }) }) : null;
}
function gk({ payload: e }) {
  const t = Fs(), n = j.useRef(null), r = j.useMemo(() => e.nodes, [e]), o = j.useMemo(() => e.edges, [e]);
  return j.useEffect(() => {
    const i = n.current;
    if (!i)
      return;
    const s = (u) => {
      const a = Nk(i, u);
      a && vm(u, a);
    }, l = (u) => {
      const a = u.target;
      a instanceof Element && a.closest(".react-flow__node, .react-flow__controls, .react-flow__panel") || fs("team-org-flow:canvas-menu", u);
    };
    return i.addEventListener("wheel", s, { capture: !0, passive: !1 }), i.addEventListener("contextmenu", l, { capture: !0 }), () => {
      i.removeEventListener("wheel", s, { capture: !0 }), i.removeEventListener("contextmenu", l, { capture: !0 });
    };
  }, []), j.useEffect(() => {
    const i = window.requestAnimationFrame(() => {
      t.fitView({ padding: 0.18, duration: 180 });
    });
    return () => window.cancelAnimationFrame(i);
  }, [e.fitKey, t]), /* @__PURE__ */ S.jsx("div", { ref: n, className: "team-org-flow-canvas", "data-team-org-flow-canvas": !0, children: /* @__PURE__ */ S.jsxs(
    F2,
    {
      nodes: r,
      edges: o,
      nodeTypes: hk,
      nodesDraggable: !1,
      nodesConnectable: !1,
      elementsSelectable: !0,
      selectNodesOnDrag: !1,
      panOnScroll: !0,
      zoomOnScroll: !0,
      zoomOnPinch: !0,
      onPaneContextMenu: (i) => {
        i.preventDefault(), fs("team-org-flow:canvas-menu", i);
      },
      onMoveStart: () => {
        window.dispatchEvent(new CustomEvent("team-org-flow:dismiss-menu"));
      },
      fitView: !0,
      minZoom: 0.25,
      maxZoom: 1.5,
      defaultEdgeOptions: {
        type: "smoothstep",
        markerEnd: { type: No.ArrowClosed }
      },
      proOptions: { hideAttribution: !0 },
      children: [
        /* @__PURE__ */ S.jsx(U2, { gap: 24, size: 1 }),
        /* @__PURE__ */ S.jsx(Z2, { position: "bottom-left", showInteractive: !1 })
      ]
    }
  ) });
}
function mk({ data: e }) {
  let t;
  return e.kind === "agent" ? t = /* @__PURE__ */ S.jsx(yk, { data: e }) : e.kind === "team" ? t = /* @__PURE__ */ S.jsx(vk, { data: e }) : e.kind === "governance" ? t = /* @__PURE__ */ S.jsx(wk, { data: e }) : t = /* @__PURE__ */ S.jsx(xk, { data: e }), /* @__PURE__ */ S.jsxs("div", { className: `team-org-flow-node team-org-flow-node--${e.kind}`, children: [
    /* @__PURE__ */ S.jsx(
      xr,
      {
        className: "team-org-flow-handle team-org-flow-handle--target",
        type: "target",
        position: Q.Top,
        isConnectable: !1
      }
    ),
    t,
    /* @__PURE__ */ S.jsx(
      xr,
      {
        className: "team-org-flow-handle team-org-flow-handle--source",
        type: "source",
        position: Q.Bottom,
        isConnectable: !1
      }
    )
  ] });
}
function yk({ data: e }) {
  const t = j.useId(), n = () => {
    window.dispatchEvent(
      new CustomEvent("team-org-flow:edit-agent", {
        detail: { agentId: e.agentId }
      })
    );
  };
  return /* @__PURE__ */ S.jsxs(
    "article",
    {
      className: e.cardClassName,
      tabIndex: 0,
      role: "button",
      "aria-label": `Edit ${e.label}`,
      "aria-describedby": t,
      onClick: n,
      onContextMenu: (r) => {
        fs("team-org-flow:agent-menu", r, { agentId: e.agentId });
      },
      onKeyDown: (r) => {
        r.key !== "Enter" && r.key !== " " || (r.preventDefault(), n());
      },
      children: [
        /* @__PURE__ */ S.jsxs("div", { className: "hierarchy-node-summary", children: [
          /* @__PURE__ */ S.jsxs("div", { className: "hierarchy-node-title", children: [
            /* @__PURE__ */ S.jsx(
              Sk,
              {
                avatar: e.avatar,
                label: e.label,
                role: e.role,
                idLabel: e.idLabel
              }
            ),
            /* @__PURE__ */ S.jsx("strong", { children: e.label })
          ] }),
          /* @__PURE__ */ S.jsxs("div", { className: "hierarchy-node-subtitle", children: [
            /* @__PURE__ */ S.jsx("span", { className: "hierarchy-node-role", children: e.role }),
            /* @__PURE__ */ S.jsx("span", { className: `state-pill ${e.status}`, children: e.status }),
            /* @__PURE__ */ S.jsx(_k, { awake: e.awake }),
            /* @__PURE__ */ S.jsx(Ek, { indicators: e.inboxIndicators })
          ] })
        ] }),
        /* @__PURE__ */ S.jsxs("span", { id: t, className: "visually-hidden", children: [
          "Agent ID ",
          e.agentId,
          ". Role ",
          e.role,
          "."
        ] }),
        /* @__PURE__ */ S.jsxs("div", { className: "org-node-work", children: [
          /* @__PURE__ */ S.jsxs("div", { className: "org-node-work-line org-node-work-line--now", children: [
            /* @__PURE__ */ S.jsx("span", { children: "Now" }),
            /* @__PURE__ */ S.jsx("strong", { children: e.work.primary })
          ] }),
          /* @__PURE__ */ S.jsxs("div", { className: "org-node-work-line org-node-work-line--waiting", children: [
            /* @__PURE__ */ S.jsx("span", { children: "Waiting" }),
            /* @__PURE__ */ S.jsx("strong", { children: e.work.waiting })
          ] })
        ] }),
        /* @__PURE__ */ S.jsx("div", { className: "hierarchy-node-details org-node-hover-panel", children: e.detailLines.map((r, o) => /* @__PURE__ */ S.jsx("div", { className: "meta-line", children: r }, `${r}-${o}`)) })
      ]
    }
  );
}
function vk({ data: e }) {
  return /* @__PURE__ */ S.jsxs("article", { className: "org-focus-team org-flow-team-node", children: [
    /* @__PURE__ */ S.jsxs("div", { className: "org-flow-team-header", children: [
      /* @__PURE__ */ S.jsxs("div", { className: "org-flow-team-title", children: [
        /* @__PURE__ */ S.jsx("p", { className: "section-label", children: e.teamId }),
        /* @__PURE__ */ S.jsx("h3", { children: e.name })
      ] }),
      /* @__PURE__ */ S.jsxs("div", { className: "org-flow-node-pills org-focus-team-status", children: [
        e.archived ? /* @__PURE__ */ S.jsx("span", { className: "state-pill archived", children: "archived" }) : null,
        e.costBlocked ? /* @__PURE__ */ S.jsx("span", { className: "state-pill warning team-cost-blocked-pill", children: "cost blocked" }) : null,
        /* @__PURE__ */ S.jsxs("span", { className: "state-pill info", children: [
          e.awakeCount,
          " awake"
        ] }),
        /* @__PURE__ */ S.jsxs("span", { className: `state-pill ${e.attentionCount ? "warning" : "accepted"}`, children: [
          e.attentionCount,
          " attention"
        ] })
      ] })
    ] }),
    /* @__PURE__ */ S.jsx($u, { html: e.costHtml }),
    /* @__PURE__ */ S.jsx($u, { html: e.costBlockedNoticeHtml })
  ] });
}
function wk({ data: e }) {
  return /* @__PURE__ */ S.jsxs(
    "article",
    {
      className: "org-flow-governance-node governance-item",
      "data-governance-group-id": e.groupId,
      onContextMenu: (t) => {
        fs("team-org-flow:governance-menu", t, { groupId: e.groupId });
      },
      children: [
        /* @__PURE__ */ S.jsxs("div", { className: "org-flow-governance-header", children: [
          /* @__PURE__ */ S.jsxs("div", { className: "org-flow-governance-title", children: [
            /* @__PURE__ */ S.jsx("p", { className: "section-label", children: "Governance" }),
            /* @__PURE__ */ S.jsx("h3", { children: e.name })
          ] }),
          /* @__PURE__ */ S.jsxs("div", { className: "org-flow-node-pills", children: [
            e.archived ? /* @__PURE__ */ S.jsx("span", { className: "state-pill blocked", children: "archived" }) : null,
            e.costAttention ? /* @__PURE__ */ S.jsx("span", { className: "state-pill warning", children: "cost attention" }) : null,
            /* @__PURE__ */ S.jsxs("span", { className: "state-pill neutral", children: [
              e.memberTeamCount,
              " team",
              e.memberTeamCount === 1 ? "" : "s"
            ] }),
            /* @__PURE__ */ S.jsxs("span", { className: `state-pill ${e.pendingAgendaCount ? "warning" : "accepted"}`, children: [
              e.pendingAgendaCount,
              " agenda"
            ] })
          ] }),
          /* @__PURE__ */ S.jsxs("div", { className: "org-flow-governance-manager", children: [
            e.managerAvatar ? /* @__PURE__ */ S.jsx(ym, { avatar: e.managerAvatar, extraClass: "agent-avatar-small" }) : null,
            /* @__PURE__ */ S.jsx("span", { children: e.managerLabel })
          ] })
        ] }),
        /* @__PURE__ */ S.jsx("div", { className: "meta-line", children: e.groupId }),
        /* @__PURE__ */ S.jsx($u, { html: e.costHtml })
      ]
    }
  );
}
function xk({ data: e }) {
  return /* @__PURE__ */ S.jsxs("article", { className: "hierarchy-node-card hierarchy-node-card--warning org-flow-warning-node", tabIndex: 0, children: [
    /* @__PURE__ */ S.jsxs("div", { className: "hierarchy-node-summary", children: [
      /* @__PURE__ */ S.jsx("div", { className: "hierarchy-node-title", children: /* @__PURE__ */ S.jsx("strong", { children: e.label }) }),
      /* @__PURE__ */ S.jsx("div", { className: "hierarchy-node-subtitle", children: /* @__PURE__ */ S.jsx("span", { className: "state-pill warning", children: "warning" }) })
    ] }),
    e.detailLines.length ? /* @__PURE__ */ S.jsx("div", { className: "hierarchy-node-details", children: e.detailLines.map((t, n) => /* @__PURE__ */ S.jsx("div", { className: "meta-line", children: t }, `${t}-${n}`)) }) : null
  ] });
}
function ym({
  avatar: e,
  extraClass: t = ""
}) {
  const n = ["agent-avatar", t].filter(Boolean).join(" ");
  return e.src ? /* @__PURE__ */ S.jsx("span", { className: n, children: /* @__PURE__ */ S.jsx("img", { src: e.src, alt: e.alt, loading: "lazy" }) }) : /* @__PURE__ */ S.jsx("span", { className: `${n} agent-avatar-fallback`, "aria-hidden": "true", children: e.initials });
}
function Sk({
  avatar: e,
  label: t,
  role: n,
  idLabel: r
}) {
  return /* @__PURE__ */ S.jsxs("span", { className: "hierarchy-node-avatar-wrap", children: [
    /* @__PURE__ */ S.jsx(ym, { avatar: e }),
    /* @__PURE__ */ S.jsxs("span", { className: "hierarchy-node-avatar-card", "aria-hidden": "true", children: [
      /* @__PURE__ */ S.jsx("strong", { className: "hierarchy-node-avatar-name", children: t }),
      r ? /* @__PURE__ */ S.jsx("span", { className: "hierarchy-node-avatar-id", children: r }) : null,
      /* @__PURE__ */ S.jsx("span", { className: "hierarchy-node-avatar-role", children: n })
    ] })
  ] });
}
function _k({ awake: e }) {
  return /* @__PURE__ */ S.jsxs("span", { className: `awake-indicator awake-indicator--${e.kind}`, title: e.detail, children: [
    /* @__PURE__ */ S.jsx("span", { className: "awake-dot", "aria-hidden": "true" }),
    e.label
  ] });
}
function Ek({ indicators: e }) {
  return /* @__PURE__ */ S.jsx("span", { className: "agent-inbox-indicators", "aria-label": "Pending inbox summary", children: e.map((t) => /* @__PURE__ */ S.jsx(kk, { indicator: t }, t.itemType)) });
}
function kk({ indicator: e }) {
  const t = j.useId(), n = j.useRef(null), r = e.items || [], o = r.length > 0;
  return j.useEffect(() => {
    const i = n.current;
    if (!i)
      return;
    const s = (l) => {
      vm(l, i);
    };
    return i.addEventListener("wheel", s, { passive: !1 }), () => {
      i.removeEventListener("wheel", s);
    };
  }, [o]), /* @__PURE__ */ S.jsxs(
    "span",
    {
      className: [
        "agent-inbox-indicator",
        `agent-inbox-indicator--${e.itemType}`,
        e.count ? "agent-inbox-indicator--active" : "agent-inbox-indicator--empty"
      ].join(" "),
      "aria-label": `${e.label}: ${e.count}`,
      "aria-describedby": o ? t : void 0,
      tabIndex: o ? 0 : void 0,
      onClick: (i) => i.stopPropagation(),
      onKeyDown: (i) => i.stopPropagation(),
      children: [
        /* @__PURE__ */ S.jsx(Ck, { direction: e.direction }),
        /* @__PURE__ */ S.jsx("strong", { children: e.count }),
        o ? /* @__PURE__ */ S.jsx(
          "span",
          {
            ref: n,
            className: "agent-inbox-popover nowheel",
            role: "tooltip",
            id: t,
            "aria-label": e.label,
            children: r.map((i, s) => /* @__PURE__ */ S.jsxs("span", { className: "agent-inbox-bubble", children: [
              /* @__PURE__ */ S.jsx("strong", { children: i.detail }),
              i.meta ? /* @__PURE__ */ S.jsx("small", { children: i.meta }) : null
            ] }, `${i.detail}-${i.meta}-${s}`))
          }
        ) : null
      ]
    }
  );
}
function Nk(e, t) {
  for (const o of Array.from(e.querySelectorAll(".agent-inbox-popover"))) {
    const i = o.getBoundingClientRect();
    if (i.width > 0 && i.height > 0 && t.clientX >= i.left && t.clientX <= i.right && t.clientY >= i.top && t.clientY <= i.bottom)
      return o;
  }
  const n = document.elementFromPoint(t.clientX, t.clientY), r = n == null ? void 0 : n.closest(".agent-inbox-popover");
  return r instanceof HTMLElement && e.contains(r) ? r : null;
}
function vm(e, t) {
  t.scrollTop += e.deltaY, e.preventDefault(), e.stopPropagation(), e.stopImmediatePropagation();
}
function Ck({ direction: e }) {
  return /* @__PURE__ */ S.jsxs("svg", { className: `agent-inbox-icon agent-inbox-icon--${e}`, viewBox: "0 0 20 20", "aria-hidden": "true", focusable: "false", children: [
    e === "up" ? /* @__PURE__ */ S.jsxs(S.Fragment, { children: [
      /* @__PURE__ */ S.jsx("path", { d: "M10 8V3" }),
      /* @__PURE__ */ S.jsx("path", { d: "M7.3 5.7 10 3l2.7 2.7" })
    ] }) : /* @__PURE__ */ S.jsxs(S.Fragment, { children: [
      /* @__PURE__ */ S.jsx("path", { d: "M10 3v5" }),
      /* @__PURE__ */ S.jsx("path", { d: "M7.3 5.3 10 8l2.7-2.7" })
    ] }),
    /* @__PURE__ */ S.jsx("path", { d: "M5.5 8.2h9a2.2 2.2 0 0 1 2.2 2.2v2.6a2.2 2.2 0 0 1-2.2 2.2H9.3L5 18v-2.8a2.2 2.2 0 0 1-1.7-2.1v-2.7a2.2 2.2 0 0 1 2.2-2.2Z" })
  ] });
}
function $u({ html: e }) {
  return e ? /* @__PURE__ */ S.jsx("div", { dangerouslySetInnerHTML: { __html: e } }) : null;
}
function fs(e, t, n = {}) {
  t.preventDefault(), t.stopPropagation(), window.dispatchEvent(
    new CustomEvent(e, {
      detail: {
        ...n,
        clientX: t.clientX,
        clientY: t.clientY
      }
    })
  );
}
function Xa(e) {
  const t = document.querySelector("#team-org-flow-root");
  if (!t) {
    window.__TEAM_ORG_FLOW_PAYLOAD__ = e;
    return;
  }
  let n = hd.get(t);
  n || (n = Mp(t), hd.set(t, n)), n.render(/* @__PURE__ */ S.jsx(pk, { payload: e }));
}
window.renderTeamOrgFlow = Xa;
window.addEventListener("team-org-flow:render", (e) => {
  Xa(e.detail ?? null);
});
window.__TEAM_ORG_FLOW_PAYLOAD__ && Xa(window.__TEAM_ORG_FLOW_PAYLOAD__);
