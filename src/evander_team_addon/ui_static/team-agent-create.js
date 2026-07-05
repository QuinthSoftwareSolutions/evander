var Nc = Object.defineProperty;
var Pc = (e, t, n) => t in e ? Nc(e, t, { enumerable: !0, configurable: !0, writable: !0, value: n }) : e[t] = n;
var Cl = (e, t, n) => Pc(e, typeof t != "symbol" ? t + "" : t, n);
var ss = { exports: {} }, ul = {}, as = { exports: {} }, R = {};
/**
 * @license React
 * react.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var bn = Symbol.for("react.element"), jc = Symbol.for("react.portal"), Lc = Symbol.for("react.fragment"), Tc = Symbol.for("react.strict_mode"), zc = Symbol.for("react.profiler"), Rc = Symbol.for("react.provider"), Mc = Symbol.for("react.context"), Ic = Symbol.for("react.forward_ref"), Dc = Symbol.for("react.suspense"), Oc = Symbol.for("react.memo"), Fc = Symbol.for("react.lazy"), Ho = Symbol.iterator;
function Uc(e) {
  return e === null || typeof e != "object" ? null : (e = Ho && e[Ho] || e["@@iterator"], typeof e == "function" ? e : null);
}
var cs = { isMounted: function() {
  return !1;
}, enqueueForceUpdate: function() {
}, enqueueReplaceState: function() {
}, enqueueSetState: function() {
} }, fs = Object.assign, ds = {};
function an(e, t, n) {
  this.props = e, this.context = t, this.refs = ds, this.updater = n || cs;
}
an.prototype.isReactComponent = {};
an.prototype.setState = function(e, t) {
  if (typeof e != "object" && typeof e != "function" && e != null) throw Error("setState(...): takes an object of state variables to update or a function which returns an object of state variables.");
  this.updater.enqueueSetState(this, e, t, "setState");
};
an.prototype.forceUpdate = function(e) {
  this.updater.enqueueForceUpdate(this, e, "forceUpdate");
};
function ps() {
}
ps.prototype = an.prototype;
function Ki(e, t, n) {
  this.props = e, this.context = t, this.refs = ds, this.updater = n || cs;
}
var Yi = Ki.prototype = new ps();
Yi.constructor = Ki;
fs(Yi, an.prototype);
Yi.isPureReactComponent = !0;
var Qo = Array.isArray, ms = Object.prototype.hasOwnProperty, Xi = { current: null }, hs = { key: !0, ref: !0, __self: !0, __source: !0 };
function vs(e, t, n) {
  var r, l = {}, i = null, o = null;
  if (t != null) for (r in t.ref !== void 0 && (o = t.ref), t.key !== void 0 && (i = "" + t.key), t) ms.call(t, r) && !hs.hasOwnProperty(r) && (l[r] = t[r]);
  var u = arguments.length - 2;
  if (u === 1) l.children = n;
  else if (1 < u) {
    for (var s = Array(u), c = 0; c < u; c++) s[c] = arguments[c + 2];
    l.children = s;
  }
  if (e && e.defaultProps) for (r in u = e.defaultProps, u) l[r] === void 0 && (l[r] = u[r]);
  return { $$typeof: bn, type: e, key: i, ref: o, props: l, _owner: Xi.current };
}
function Ac(e, t) {
  return { $$typeof: bn, type: e.type, key: t, ref: e.ref, props: e.props, _owner: e._owner };
}
function Gi(e) {
  return typeof e == "object" && e !== null && e.$$typeof === bn;
}
function Vc(e) {
  var t = { "=": "=0", ":": "=2" };
  return "$" + e.replace(/[=:]/g, function(n) {
    return t[n];
  });
}
var Wo = /\/+/g;
function _l(e, t) {
  return typeof e == "object" && e !== null && e.key != null ? Vc("" + e.key) : t.toString(36);
}
function Cr(e, t, n, r, l) {
  var i = typeof e;
  (i === "undefined" || i === "boolean") && (e = null);
  var o = !1;
  if (e === null) o = !0;
  else switch (i) {
    case "string":
    case "number":
      o = !0;
      break;
    case "object":
      switch (e.$$typeof) {
        case bn:
        case jc:
          o = !0;
      }
  }
  if (o) return o = e, l = l(o), e = r === "" ? "." + _l(o, 0) : r, Qo(l) ? (n = "", e != null && (n = e.replace(Wo, "$&/") + "/"), Cr(l, t, n, "", function(c) {
    return c;
  })) : l != null && (Gi(l) && (l = Ac(l, n + (!l.key || o && o.key === l.key ? "" : ("" + l.key).replace(Wo, "$&/") + "/") + e)), t.push(l)), 1;
  if (o = 0, r = r === "" ? "." : r + ":", Qo(e)) for (var u = 0; u < e.length; u++) {
    i = e[u];
    var s = r + _l(i, u);
    o += Cr(i, t, n, s, l);
  }
  else if (s = Uc(e), typeof s == "function") for (e = s.call(e), u = 0; !(i = e.next()).done; ) i = i.value, s = r + _l(i, u++), o += Cr(i, t, n, s, l);
  else if (i === "object") throw t = String(e), Error("Objects are not valid as a React child (found: " + (t === "[object Object]" ? "object with keys {" + Object.keys(e).join(", ") + "}" : t) + "). If you meant to render a collection of children, use an array instead.");
  return o;
}
function ur(e, t, n) {
  if (e == null) return e;
  var r = [], l = 0;
  return Cr(e, r, "", "", function(i) {
    return t.call(n, i, l++);
  }), r;
}
function $c(e) {
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
var de = { current: null }, _r = { transition: null }, Bc = { ReactCurrentDispatcher: de, ReactCurrentBatchConfig: _r, ReactCurrentOwner: Xi };
function gs() {
  throw Error("act(...) is not supported in production builds of React.");
}
R.Children = { map: ur, forEach: function(e, t, n) {
  ur(e, function() {
    t.apply(this, arguments);
  }, n);
}, count: function(e) {
  var t = 0;
  return ur(e, function() {
    t++;
  }), t;
}, toArray: function(e) {
  return ur(e, function(t) {
    return t;
  }) || [];
}, only: function(e) {
  if (!Gi(e)) throw Error("React.Children.only expected to receive a single React element child.");
  return e;
} };
R.Component = an;
R.Fragment = Lc;
R.Profiler = zc;
R.PureComponent = Ki;
R.StrictMode = Tc;
R.Suspense = Dc;
R.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED = Bc;
R.act = gs;
R.cloneElement = function(e, t, n) {
  if (e == null) throw Error("React.cloneElement(...): The argument must be a React element, but you passed " + e + ".");
  var r = fs({}, e.props), l = e.key, i = e.ref, o = e._owner;
  if (t != null) {
    if (t.ref !== void 0 && (i = t.ref, o = Xi.current), t.key !== void 0 && (l = "" + t.key), e.type && e.type.defaultProps) var u = e.type.defaultProps;
    for (s in t) ms.call(t, s) && !hs.hasOwnProperty(s) && (r[s] = t[s] === void 0 && u !== void 0 ? u[s] : t[s]);
  }
  var s = arguments.length - 2;
  if (s === 1) r.children = n;
  else if (1 < s) {
    u = Array(s);
    for (var c = 0; c < s; c++) u[c] = arguments[c + 2];
    r.children = u;
  }
  return { $$typeof: bn, type: e.type, key: l, ref: i, props: r, _owner: o };
};
R.createContext = function(e) {
  return e = { $$typeof: Mc, _currentValue: e, _currentValue2: e, _threadCount: 0, Provider: null, Consumer: null, _defaultValue: null, _globalName: null }, e.Provider = { $$typeof: Rc, _context: e }, e.Consumer = e;
};
R.createElement = vs;
R.createFactory = function(e) {
  var t = vs.bind(null, e);
  return t.type = e, t;
};
R.createRef = function() {
  return { current: null };
};
R.forwardRef = function(e) {
  return { $$typeof: Ic, render: e };
};
R.isValidElement = Gi;
R.lazy = function(e) {
  return { $$typeof: Fc, _payload: { _status: -1, _result: e }, _init: $c };
};
R.memo = function(e, t) {
  return { $$typeof: Oc, type: e, compare: t === void 0 ? null : t };
};
R.startTransition = function(e) {
  var t = _r.transition;
  _r.transition = {};
  try {
    e();
  } finally {
    _r.transition = t;
  }
};
R.unstable_act = gs;
R.useCallback = function(e, t) {
  return de.current.useCallback(e, t);
};
R.useContext = function(e) {
  return de.current.useContext(e);
};
R.useDebugValue = function() {
};
R.useDeferredValue = function(e) {
  return de.current.useDeferredValue(e);
};
R.useEffect = function(e, t) {
  return de.current.useEffect(e, t);
};
R.useId = function() {
  return de.current.useId();
};
R.useImperativeHandle = function(e, t, n) {
  return de.current.useImperativeHandle(e, t, n);
};
R.useInsertionEffect = function(e, t) {
  return de.current.useInsertionEffect(e, t);
};
R.useLayoutEffect = function(e, t) {
  return de.current.useLayoutEffect(e, t);
};
R.useMemo = function(e, t) {
  return de.current.useMemo(e, t);
};
R.useReducer = function(e, t, n) {
  return de.current.useReducer(e, t, n);
};
R.useRef = function(e) {
  return de.current.useRef(e);
};
R.useState = function(e) {
  return de.current.useState(e);
};
R.useSyncExternalStore = function(e, t, n) {
  return de.current.useSyncExternalStore(e, t, n);
};
R.useTransition = function() {
  return de.current.useTransition();
};
R.version = "18.3.1";
as.exports = R;
var D = as.exports;
/**
 * @license React
 * react-jsx-runtime.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var Hc = D, Qc = Symbol.for("react.element"), Wc = Symbol.for("react.fragment"), Kc = Object.prototype.hasOwnProperty, Yc = Hc.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED.ReactCurrentOwner, Xc = { key: !0, ref: !0, __self: !0, __source: !0 };
function ys(e, t, n) {
  var r, l = {}, i = null, o = null;
  n !== void 0 && (i = "" + n), t.key !== void 0 && (i = "" + t.key), t.ref !== void 0 && (o = t.ref);
  for (r in t) Kc.call(t, r) && !Xc.hasOwnProperty(r) && (l[r] = t[r]);
  if (e && e.defaultProps) for (r in t = e.defaultProps, t) l[r] === void 0 && (l[r] = t[r]);
  return { $$typeof: Qc, type: e, key: i, ref: o, props: l, _owner: Yc.current };
}
ul.Fragment = Wc;
ul.jsx = ys;
ul.jsxs = ys;
ss.exports = ul;
var m = ss.exports, ws = { exports: {} }, Ee = {}, ks = { exports: {} }, Ss = {};
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
  function t(j, T) {
    var z = j.length;
    j.push(T);
    e: for (; 0 < z; ) {
      var X = z - 1 >>> 1, b = j[X];
      if (0 < l(b, T)) j[X] = T, j[z] = b, z = X;
      else break e;
    }
  }
  function n(j) {
    return j.length === 0 ? null : j[0];
  }
  function r(j) {
    if (j.length === 0) return null;
    var T = j[0], z = j.pop();
    if (z !== T) {
      j[0] = z;
      e: for (var X = 0, b = j.length, ir = b >>> 1; X < ir; ) {
        var kt = 2 * (X + 1) - 1, El = j[kt], St = kt + 1, or = j[St];
        if (0 > l(El, z)) St < b && 0 > l(or, El) ? (j[X] = or, j[St] = z, X = St) : (j[X] = El, j[kt] = z, X = kt);
        else if (St < b && 0 > l(or, z)) j[X] = or, j[St] = z, X = St;
        else break e;
      }
    }
    return T;
  }
  function l(j, T) {
    var z = j.sortIndex - T.sortIndex;
    return z !== 0 ? z : j.id - T.id;
  }
  if (typeof performance == "object" && typeof performance.now == "function") {
    var i = performance;
    e.unstable_now = function() {
      return i.now();
    };
  } else {
    var o = Date, u = o.now();
    e.unstable_now = function() {
      return o.now() - u;
    };
  }
  var s = [], c = [], h = 1, v = null, f = 3, w = !1, k = !1, E = !1, M = typeof setTimeout == "function" ? setTimeout : null, d = typeof clearTimeout == "function" ? clearTimeout : null, a = typeof setImmediate < "u" ? setImmediate : null;
  typeof navigator < "u" && navigator.scheduling !== void 0 && navigator.scheduling.isInputPending !== void 0 && navigator.scheduling.isInputPending.bind(navigator.scheduling);
  function p(j) {
    for (var T = n(c); T !== null; ) {
      if (T.callback === null) r(c);
      else if (T.startTime <= j) r(c), T.sortIndex = T.expirationTime, t(s, T);
      else break;
      T = n(c);
    }
  }
  function g(j) {
    if (E = !1, p(j), !k) if (n(s) !== null) k = !0, pn(C);
    else {
      var T = n(c);
      T !== null && mn(g, T.startTime - j);
    }
  }
  function C(j, T) {
    k = !1, E && (E = !1, d(N), N = -1), w = !0;
    var z = f;
    try {
      for (p(T), v = n(s); v !== null && (!(v.expirationTime > T) || j && !P()); ) {
        var X = v.callback;
        if (typeof X == "function") {
          v.callback = null, f = v.priorityLevel;
          var b = X(v.expirationTime <= T);
          T = e.unstable_now(), typeof b == "function" ? v.callback = b : v === n(s) && r(s), p(T);
        } else r(s);
        v = n(s);
      }
      if (v !== null) var ir = !0;
      else {
        var kt = n(c);
        kt !== null && mn(g, kt.startTime - T), ir = !1;
      }
      return ir;
    } finally {
      v = null, f = z, w = !1;
    }
  }
  var _ = !1, x = null, N = -1, I = 5, y = -1;
  function P() {
    return !(e.unstable_now() - y < I);
  }
  function A() {
    if (x !== null) {
      var j = e.unstable_now();
      y = j;
      var T = !0;
      try {
        T = x(!0, j);
      } finally {
        T ? ae() : (_ = !1, x = null);
      }
    } else _ = !1;
  }
  var ae;
  if (typeof a == "function") ae = function() {
    a(A);
  };
  else if (typeof MessageChannel < "u") {
    var dn = new MessageChannel(), lr = dn.port2;
    dn.port1.onmessage = A, ae = function() {
      lr.postMessage(null);
    };
  } else ae = function() {
    M(A, 0);
  };
  function pn(j) {
    x = j, _ || (_ = !0, ae());
  }
  function mn(j, T) {
    N = M(function() {
      j(e.unstable_now());
    }, T);
  }
  e.unstable_IdlePriority = 5, e.unstable_ImmediatePriority = 1, e.unstable_LowPriority = 4, e.unstable_NormalPriority = 3, e.unstable_Profiling = null, e.unstable_UserBlockingPriority = 2, e.unstable_cancelCallback = function(j) {
    j.callback = null;
  }, e.unstable_continueExecution = function() {
    k || w || (k = !0, pn(C));
  }, e.unstable_forceFrameRate = function(j) {
    0 > j || 125 < j ? console.error("forceFrameRate takes a positive int between 0 and 125, forcing frame rates higher than 125 fps is not supported") : I = 0 < j ? Math.floor(1e3 / j) : 5;
  }, e.unstable_getCurrentPriorityLevel = function() {
    return f;
  }, e.unstable_getFirstCallbackNode = function() {
    return n(s);
  }, e.unstable_next = function(j) {
    switch (f) {
      case 1:
      case 2:
      case 3:
        var T = 3;
        break;
      default:
        T = f;
    }
    var z = f;
    f = T;
    try {
      return j();
    } finally {
      f = z;
    }
  }, e.unstable_pauseExecution = function() {
  }, e.unstable_requestPaint = function() {
  }, e.unstable_runWithPriority = function(j, T) {
    switch (j) {
      case 1:
      case 2:
      case 3:
      case 4:
      case 5:
        break;
      default:
        j = 3;
    }
    var z = f;
    f = j;
    try {
      return T();
    } finally {
      f = z;
    }
  }, e.unstable_scheduleCallback = function(j, T, z) {
    var X = e.unstable_now();
    switch (typeof z == "object" && z !== null ? (z = z.delay, z = typeof z == "number" && 0 < z ? X + z : X) : z = X, j) {
      case 1:
        var b = -1;
        break;
      case 2:
        b = 250;
        break;
      case 5:
        b = 1073741823;
        break;
      case 4:
        b = 1e4;
        break;
      default:
        b = 5e3;
    }
    return b = z + b, j = { id: h++, callback: T, priorityLevel: j, startTime: z, expirationTime: b, sortIndex: -1 }, z > X ? (j.sortIndex = z, t(c, j), n(s) === null && j === n(c) && (E ? (d(N), N = -1) : E = !0, mn(g, z - X))) : (j.sortIndex = b, t(s, j), k || w || (k = !0, pn(C))), j;
  }, e.unstable_shouldYield = P, e.unstable_wrapCallback = function(j) {
    var T = f;
    return function() {
      var z = f;
      f = T;
      try {
        return j.apply(this, arguments);
      } finally {
        f = z;
      }
    };
  };
})(Ss);
ks.exports = Ss;
var Gc = ks.exports;
/**
 * @license React
 * react-dom.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var Zc = D, xe = Gc;
function S(e) {
  for (var t = "https://reactjs.org/docs/error-decoder.html?invariant=" + e, n = 1; n < arguments.length; n++) t += "&args[]=" + encodeURIComponent(arguments[n]);
  return "Minified React error #" + e + "; visit " + t + " for the full message or use the non-minified dev environment for full errors and additional helpful warnings.";
}
var xs = /* @__PURE__ */ new Set(), On = {};
function It(e, t) {
  tn(e, t), tn(e + "Capture", t);
}
function tn(e, t) {
  for (On[e] = t, e = 0; e < t.length; e++) xs.add(t[e]);
}
var Ge = !(typeof window > "u" || typeof window.document > "u" || typeof window.document.createElement > "u"), ei = Object.prototype.hasOwnProperty, Jc = /^[:A-Z_a-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF\u0370-\u037D\u037F-\u1FFF\u200C-\u200D\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD][:A-Z_a-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF\u0370-\u037D\u037F-\u1FFF\u200C-\u200D\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD\-.0-9\u00B7\u0300-\u036F\u203F-\u2040]*$/, Ko = {}, Yo = {};
function qc(e) {
  return ei.call(Yo, e) ? !0 : ei.call(Ko, e) ? !1 : Jc.test(e) ? Yo[e] = !0 : (Ko[e] = !0, !1);
}
function bc(e, t, n, r) {
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
function ef(e, t, n, r) {
  if (t === null || typeof t > "u" || bc(e, t, n, r)) return !0;
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
function pe(e, t, n, r, l, i, o) {
  this.acceptsBooleans = t === 2 || t === 3 || t === 4, this.attributeName = r, this.attributeNamespace = l, this.mustUseProperty = n, this.propertyName = e, this.type = t, this.sanitizeURL = i, this.removeEmptyString = o;
}
var le = {};
"children dangerouslySetInnerHTML defaultValue defaultChecked innerHTML suppressContentEditableWarning suppressHydrationWarning style".split(" ").forEach(function(e) {
  le[e] = new pe(e, 0, !1, e, null, !1, !1);
});
[["acceptCharset", "accept-charset"], ["className", "class"], ["htmlFor", "for"], ["httpEquiv", "http-equiv"]].forEach(function(e) {
  var t = e[0];
  le[t] = new pe(t, 1, !1, e[1], null, !1, !1);
});
["contentEditable", "draggable", "spellCheck", "value"].forEach(function(e) {
  le[e] = new pe(e, 2, !1, e.toLowerCase(), null, !1, !1);
});
["autoReverse", "externalResourcesRequired", "focusable", "preserveAlpha"].forEach(function(e) {
  le[e] = new pe(e, 2, !1, e, null, !1, !1);
});
"allowFullScreen async autoFocus autoPlay controls default defer disabled disablePictureInPicture disableRemotePlayback formNoValidate hidden loop noModule noValidate open playsInline readOnly required reversed scoped seamless itemScope".split(" ").forEach(function(e) {
  le[e] = new pe(e, 3, !1, e.toLowerCase(), null, !1, !1);
});
["checked", "multiple", "muted", "selected"].forEach(function(e) {
  le[e] = new pe(e, 3, !0, e, null, !1, !1);
});
["capture", "download"].forEach(function(e) {
  le[e] = new pe(e, 4, !1, e, null, !1, !1);
});
["cols", "rows", "size", "span"].forEach(function(e) {
  le[e] = new pe(e, 6, !1, e, null, !1, !1);
});
["rowSpan", "start"].forEach(function(e) {
  le[e] = new pe(e, 5, !1, e.toLowerCase(), null, !1, !1);
});
var Zi = /[\-:]([a-z])/g;
function Ji(e) {
  return e[1].toUpperCase();
}
"accent-height alignment-baseline arabic-form baseline-shift cap-height clip-path clip-rule color-interpolation color-interpolation-filters color-profile color-rendering dominant-baseline enable-background fill-opacity fill-rule flood-color flood-opacity font-family font-size font-size-adjust font-stretch font-style font-variant font-weight glyph-name glyph-orientation-horizontal glyph-orientation-vertical horiz-adv-x horiz-origin-x image-rendering letter-spacing lighting-color marker-end marker-mid marker-start overline-position overline-thickness paint-order panose-1 pointer-events rendering-intent shape-rendering stop-color stop-opacity strikethrough-position strikethrough-thickness stroke-dasharray stroke-dashoffset stroke-linecap stroke-linejoin stroke-miterlimit stroke-opacity stroke-width text-anchor text-decoration text-rendering underline-position underline-thickness unicode-bidi unicode-range units-per-em v-alphabetic v-hanging v-ideographic v-mathematical vector-effect vert-adv-y vert-origin-x vert-origin-y word-spacing writing-mode xmlns:xlink x-height".split(" ").forEach(function(e) {
  var t = e.replace(
    Zi,
    Ji
  );
  le[t] = new pe(t, 1, !1, e, null, !1, !1);
});
"xlink:actuate xlink:arcrole xlink:role xlink:show xlink:title xlink:type".split(" ").forEach(function(e) {
  var t = e.replace(Zi, Ji);
  le[t] = new pe(t, 1, !1, e, "http://www.w3.org/1999/xlink", !1, !1);
});
["xml:base", "xml:lang", "xml:space"].forEach(function(e) {
  var t = e.replace(Zi, Ji);
  le[t] = new pe(t, 1, !1, e, "http://www.w3.org/XML/1998/namespace", !1, !1);
});
["tabIndex", "crossOrigin"].forEach(function(e) {
  le[e] = new pe(e, 1, !1, e.toLowerCase(), null, !1, !1);
});
le.xlinkHref = new pe("xlinkHref", 1, !1, "xlink:href", "http://www.w3.org/1999/xlink", !0, !1);
["src", "href", "action", "formAction"].forEach(function(e) {
  le[e] = new pe(e, 1, !1, e.toLowerCase(), null, !0, !0);
});
function qi(e, t, n, r) {
  var l = le.hasOwnProperty(t) ? le[t] : null;
  (l !== null ? l.type !== 0 : r || !(2 < t.length) || t[0] !== "o" && t[0] !== "O" || t[1] !== "n" && t[1] !== "N") && (ef(t, n, l, r) && (n = null), r || l === null ? qc(t) && (n === null ? e.removeAttribute(t) : e.setAttribute(t, "" + n)) : l.mustUseProperty ? e[l.propertyName] = n === null ? l.type === 3 ? !1 : "" : n : (t = l.attributeName, r = l.attributeNamespace, n === null ? e.removeAttribute(t) : (l = l.type, n = l === 3 || l === 4 && n === !0 ? "" : "" + n, r ? e.setAttributeNS(r, t, n) : e.setAttribute(t, n))));
}
var be = Zc.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED, sr = Symbol.for("react.element"), Ft = Symbol.for("react.portal"), Ut = Symbol.for("react.fragment"), bi = Symbol.for("react.strict_mode"), ti = Symbol.for("react.profiler"), Es = Symbol.for("react.provider"), Cs = Symbol.for("react.context"), eo = Symbol.for("react.forward_ref"), ni = Symbol.for("react.suspense"), ri = Symbol.for("react.suspense_list"), to = Symbol.for("react.memo"), tt = Symbol.for("react.lazy"), _s = Symbol.for("react.offscreen"), Xo = Symbol.iterator;
function hn(e) {
  return e === null || typeof e != "object" ? null : (e = Xo && e[Xo] || e["@@iterator"], typeof e == "function" ? e : null);
}
var K = Object.assign, Nl;
function En(e) {
  if (Nl === void 0) try {
    throw Error();
  } catch (n) {
    var t = n.stack.trim().match(/\n( *(at )?)/);
    Nl = t && t[1] || "";
  }
  return `
` + Nl + e;
}
var Pl = !1;
function jl(e, t) {
  if (!e || Pl) return "";
  Pl = !0;
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
      } catch (c) {
        var r = c;
      }
      Reflect.construct(e, [], t);
    } else {
      try {
        t.call();
      } catch (c) {
        r = c;
      }
      e.call(t.prototype);
    }
    else {
      try {
        throw Error();
      } catch (c) {
        r = c;
      }
      e();
    }
  } catch (c) {
    if (c && r && typeof c.stack == "string") {
      for (var l = c.stack.split(`
`), i = r.stack.split(`
`), o = l.length - 1, u = i.length - 1; 1 <= o && 0 <= u && l[o] !== i[u]; ) u--;
      for (; 1 <= o && 0 <= u; o--, u--) if (l[o] !== i[u]) {
        if (o !== 1 || u !== 1)
          do
            if (o--, u--, 0 > u || l[o] !== i[u]) {
              var s = `
` + l[o].replace(" at new ", " at ");
              return e.displayName && s.includes("<anonymous>") && (s = s.replace("<anonymous>", e.displayName)), s;
            }
          while (1 <= o && 0 <= u);
        break;
      }
    }
  } finally {
    Pl = !1, Error.prepareStackTrace = n;
  }
  return (e = e ? e.displayName || e.name : "") ? En(e) : "";
}
function tf(e) {
  switch (e.tag) {
    case 5:
      return En(e.type);
    case 16:
      return En("Lazy");
    case 13:
      return En("Suspense");
    case 19:
      return En("SuspenseList");
    case 0:
    case 2:
    case 15:
      return e = jl(e.type, !1), e;
    case 11:
      return e = jl(e.type.render, !1), e;
    case 1:
      return e = jl(e.type, !0), e;
    default:
      return "";
  }
}
function li(e) {
  if (e == null) return null;
  if (typeof e == "function") return e.displayName || e.name || null;
  if (typeof e == "string") return e;
  switch (e) {
    case Ut:
      return "Fragment";
    case Ft:
      return "Portal";
    case ti:
      return "Profiler";
    case bi:
      return "StrictMode";
    case ni:
      return "Suspense";
    case ri:
      return "SuspenseList";
  }
  if (typeof e == "object") switch (e.$$typeof) {
    case Cs:
      return (e.displayName || "Context") + ".Consumer";
    case Es:
      return (e._context.displayName || "Context") + ".Provider";
    case eo:
      var t = e.render;
      return e = e.displayName, e || (e = t.displayName || t.name || "", e = e !== "" ? "ForwardRef(" + e + ")" : "ForwardRef"), e;
    case to:
      return t = e.displayName || null, t !== null ? t : li(e.type) || "Memo";
    case tt:
      t = e._payload, e = e._init;
      try {
        return li(e(t));
      } catch {
      }
  }
  return null;
}
function nf(e) {
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
      return li(t);
    case 8:
      return t === bi ? "StrictMode" : "Mode";
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
function ht(e) {
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
function Ns(e) {
  var t = e.type;
  return (e = e.nodeName) && e.toLowerCase() === "input" && (t === "checkbox" || t === "radio");
}
function rf(e) {
  var t = Ns(e) ? "checked" : "value", n = Object.getOwnPropertyDescriptor(e.constructor.prototype, t), r = "" + e[t];
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
function ar(e) {
  e._valueTracker || (e._valueTracker = rf(e));
}
function Ps(e) {
  if (!e) return !1;
  var t = e._valueTracker;
  if (!t) return !0;
  var n = t.getValue(), r = "";
  return e && (r = Ns(e) ? e.checked ? "true" : "false" : e.value), e = r, e !== n ? (t.setValue(e), !0) : !1;
}
function Fr(e) {
  if (e = e || (typeof document < "u" ? document : void 0), typeof e > "u") return null;
  try {
    return e.activeElement || e.body;
  } catch {
    return e.body;
  }
}
function ii(e, t) {
  var n = t.checked;
  return K({}, t, { defaultChecked: void 0, defaultValue: void 0, value: void 0, checked: n ?? e._wrapperState.initialChecked });
}
function Go(e, t) {
  var n = t.defaultValue == null ? "" : t.defaultValue, r = t.checked != null ? t.checked : t.defaultChecked;
  n = ht(t.value != null ? t.value : n), e._wrapperState = { initialChecked: r, initialValue: n, controlled: t.type === "checkbox" || t.type === "radio" ? t.checked != null : t.value != null };
}
function js(e, t) {
  t = t.checked, t != null && qi(e, "checked", t, !1);
}
function oi(e, t) {
  js(e, t);
  var n = ht(t.value), r = t.type;
  if (n != null) r === "number" ? (n === 0 && e.value === "" || e.value != n) && (e.value = "" + n) : e.value !== "" + n && (e.value = "" + n);
  else if (r === "submit" || r === "reset") {
    e.removeAttribute("value");
    return;
  }
  t.hasOwnProperty("value") ? ui(e, t.type, n) : t.hasOwnProperty("defaultValue") && ui(e, t.type, ht(t.defaultValue)), t.checked == null && t.defaultChecked != null && (e.defaultChecked = !!t.defaultChecked);
}
function Zo(e, t, n) {
  if (t.hasOwnProperty("value") || t.hasOwnProperty("defaultValue")) {
    var r = t.type;
    if (!(r !== "submit" && r !== "reset" || t.value !== void 0 && t.value !== null)) return;
    t = "" + e._wrapperState.initialValue, n || t === e.value || (e.value = t), e.defaultValue = t;
  }
  n = e.name, n !== "" && (e.name = ""), e.defaultChecked = !!e._wrapperState.initialChecked, n !== "" && (e.name = n);
}
function ui(e, t, n) {
  (t !== "number" || Fr(e.ownerDocument) !== e) && (n == null ? e.defaultValue = "" + e._wrapperState.initialValue : e.defaultValue !== "" + n && (e.defaultValue = "" + n));
}
var Cn = Array.isArray;
function Gt(e, t, n, r) {
  if (e = e.options, t) {
    t = {};
    for (var l = 0; l < n.length; l++) t["$" + n[l]] = !0;
    for (n = 0; n < e.length; n++) l = t.hasOwnProperty("$" + e[n].value), e[n].selected !== l && (e[n].selected = l), l && r && (e[n].defaultSelected = !0);
  } else {
    for (n = "" + ht(n), t = null, l = 0; l < e.length; l++) {
      if (e[l].value === n) {
        e[l].selected = !0, r && (e[l].defaultSelected = !0);
        return;
      }
      t !== null || e[l].disabled || (t = e[l]);
    }
    t !== null && (t.selected = !0);
  }
}
function si(e, t) {
  if (t.dangerouslySetInnerHTML != null) throw Error(S(91));
  return K({}, t, { value: void 0, defaultValue: void 0, children: "" + e._wrapperState.initialValue });
}
function Jo(e, t) {
  var n = t.value;
  if (n == null) {
    if (n = t.children, t = t.defaultValue, n != null) {
      if (t != null) throw Error(S(92));
      if (Cn(n)) {
        if (1 < n.length) throw Error(S(93));
        n = n[0];
      }
      t = n;
    }
    t == null && (t = ""), n = t;
  }
  e._wrapperState = { initialValue: ht(n) };
}
function Ls(e, t) {
  var n = ht(t.value), r = ht(t.defaultValue);
  n != null && (n = "" + n, n !== e.value && (e.value = n), t.defaultValue == null && e.defaultValue !== n && (e.defaultValue = n)), r != null && (e.defaultValue = "" + r);
}
function qo(e) {
  var t = e.textContent;
  t === e._wrapperState.initialValue && t !== "" && t !== null && (e.value = t);
}
function Ts(e) {
  switch (e) {
    case "svg":
      return "http://www.w3.org/2000/svg";
    case "math":
      return "http://www.w3.org/1998/Math/MathML";
    default:
      return "http://www.w3.org/1999/xhtml";
  }
}
function ai(e, t) {
  return e == null || e === "http://www.w3.org/1999/xhtml" ? Ts(t) : e === "http://www.w3.org/2000/svg" && t === "foreignObject" ? "http://www.w3.org/1999/xhtml" : e;
}
var cr, zs = function(e) {
  return typeof MSApp < "u" && MSApp.execUnsafeLocalFunction ? function(t, n, r, l) {
    MSApp.execUnsafeLocalFunction(function() {
      return e(t, n, r, l);
    });
  } : e;
}(function(e, t) {
  if (e.namespaceURI !== "http://www.w3.org/2000/svg" || "innerHTML" in e) e.innerHTML = t;
  else {
    for (cr = cr || document.createElement("div"), cr.innerHTML = "<svg>" + t.valueOf().toString() + "</svg>", t = cr.firstChild; e.firstChild; ) e.removeChild(e.firstChild);
    for (; t.firstChild; ) e.appendChild(t.firstChild);
  }
});
function Fn(e, t) {
  if (t) {
    var n = e.firstChild;
    if (n && n === e.lastChild && n.nodeType === 3) {
      n.nodeValue = t;
      return;
    }
  }
  e.textContent = t;
}
var Pn = {
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
}, lf = ["Webkit", "ms", "Moz", "O"];
Object.keys(Pn).forEach(function(e) {
  lf.forEach(function(t) {
    t = t + e.charAt(0).toUpperCase() + e.substring(1), Pn[t] = Pn[e];
  });
});
function Rs(e, t, n) {
  return t == null || typeof t == "boolean" || t === "" ? "" : n || typeof t != "number" || t === 0 || Pn.hasOwnProperty(e) && Pn[e] ? ("" + t).trim() : t + "px";
}
function Ms(e, t) {
  e = e.style;
  for (var n in t) if (t.hasOwnProperty(n)) {
    var r = n.indexOf("--") === 0, l = Rs(n, t[n], r);
    n === "float" && (n = "cssFloat"), r ? e.setProperty(n, l) : e[n] = l;
  }
}
var of = K({ menuitem: !0 }, { area: !0, base: !0, br: !0, col: !0, embed: !0, hr: !0, img: !0, input: !0, keygen: !0, link: !0, meta: !0, param: !0, source: !0, track: !0, wbr: !0 });
function ci(e, t) {
  if (t) {
    if (of[e] && (t.children != null || t.dangerouslySetInnerHTML != null)) throw Error(S(137, e));
    if (t.dangerouslySetInnerHTML != null) {
      if (t.children != null) throw Error(S(60));
      if (typeof t.dangerouslySetInnerHTML != "object" || !("__html" in t.dangerouslySetInnerHTML)) throw Error(S(61));
    }
    if (t.style != null && typeof t.style != "object") throw Error(S(62));
  }
}
function fi(e, t) {
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
var di = null;
function no(e) {
  return e = e.target || e.srcElement || window, e.correspondingUseElement && (e = e.correspondingUseElement), e.nodeType === 3 ? e.parentNode : e;
}
var pi = null, Zt = null, Jt = null;
function bo(e) {
  if (e = nr(e)) {
    if (typeof pi != "function") throw Error(S(280));
    var t = e.stateNode;
    t && (t = dl(t), pi(e.stateNode, e.type, t));
  }
}
function Is(e) {
  Zt ? Jt ? Jt.push(e) : Jt = [e] : Zt = e;
}
function Ds() {
  if (Zt) {
    var e = Zt, t = Jt;
    if (Jt = Zt = null, bo(e), t) for (e = 0; e < t.length; e++) bo(t[e]);
  }
}
function Os(e, t) {
  return e(t);
}
function Fs() {
}
var Ll = !1;
function Us(e, t, n) {
  if (Ll) return e(t, n);
  Ll = !0;
  try {
    return Os(e, t, n);
  } finally {
    Ll = !1, (Zt !== null || Jt !== null) && (Fs(), Ds());
  }
}
function Un(e, t) {
  var n = e.stateNode;
  if (n === null) return null;
  var r = dl(n);
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
  if (n && typeof n != "function") throw Error(S(231, t, typeof n));
  return n;
}
var mi = !1;
if (Ge) try {
  var vn = {};
  Object.defineProperty(vn, "passive", { get: function() {
    mi = !0;
  } }), window.addEventListener("test", vn, vn), window.removeEventListener("test", vn, vn);
} catch {
  mi = !1;
}
function uf(e, t, n, r, l, i, o, u, s) {
  var c = Array.prototype.slice.call(arguments, 3);
  try {
    t.apply(n, c);
  } catch (h) {
    this.onError(h);
  }
}
var jn = !1, Ur = null, Ar = !1, hi = null, sf = { onError: function(e) {
  jn = !0, Ur = e;
} };
function af(e, t, n, r, l, i, o, u, s) {
  jn = !1, Ur = null, uf.apply(sf, arguments);
}
function cf(e, t, n, r, l, i, o, u, s) {
  if (af.apply(this, arguments), jn) {
    if (jn) {
      var c = Ur;
      jn = !1, Ur = null;
    } else throw Error(S(198));
    Ar || (Ar = !0, hi = c);
  }
}
function Dt(e) {
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
function As(e) {
  if (e.tag === 13) {
    var t = e.memoizedState;
    if (t === null && (e = e.alternate, e !== null && (t = e.memoizedState)), t !== null) return t.dehydrated;
  }
  return null;
}
function eu(e) {
  if (Dt(e) !== e) throw Error(S(188));
}
function ff(e) {
  var t = e.alternate;
  if (!t) {
    if (t = Dt(e), t === null) throw Error(S(188));
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
        if (i === n) return eu(l), e;
        if (i === r) return eu(l), t;
        i = i.sibling;
      }
      throw Error(S(188));
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
        if (!o) throw Error(S(189));
      }
    }
    if (n.alternate !== r) throw Error(S(190));
  }
  if (n.tag !== 3) throw Error(S(188));
  return n.stateNode.current === n ? e : t;
}
function Vs(e) {
  return e = ff(e), e !== null ? $s(e) : null;
}
function $s(e) {
  if (e.tag === 5 || e.tag === 6) return e;
  for (e = e.child; e !== null; ) {
    var t = $s(e);
    if (t !== null) return t;
    e = e.sibling;
  }
  return null;
}
var Bs = xe.unstable_scheduleCallback, tu = xe.unstable_cancelCallback, df = xe.unstable_shouldYield, pf = xe.unstable_requestPaint, G = xe.unstable_now, mf = xe.unstable_getCurrentPriorityLevel, ro = xe.unstable_ImmediatePriority, Hs = xe.unstable_UserBlockingPriority, Vr = xe.unstable_NormalPriority, hf = xe.unstable_LowPriority, Qs = xe.unstable_IdlePriority, sl = null, Be = null;
function vf(e) {
  if (Be && typeof Be.onCommitFiberRoot == "function") try {
    Be.onCommitFiberRoot(sl, e, void 0, (e.current.flags & 128) === 128);
  } catch {
  }
}
var Oe = Math.clz32 ? Math.clz32 : wf, gf = Math.log, yf = Math.LN2;
function wf(e) {
  return e >>>= 0, e === 0 ? 32 : 31 - (gf(e) / yf | 0) | 0;
}
var fr = 64, dr = 4194304;
function _n(e) {
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
function $r(e, t) {
  var n = e.pendingLanes;
  if (n === 0) return 0;
  var r = 0, l = e.suspendedLanes, i = e.pingedLanes, o = n & 268435455;
  if (o !== 0) {
    var u = o & ~l;
    u !== 0 ? r = _n(u) : (i &= o, i !== 0 && (r = _n(i)));
  } else o = n & ~l, o !== 0 ? r = _n(o) : i !== 0 && (r = _n(i));
  if (r === 0) return 0;
  if (t !== 0 && t !== r && !(t & l) && (l = r & -r, i = t & -t, l >= i || l === 16 && (i & 4194240) !== 0)) return t;
  if (r & 4 && (r |= n & 16), t = e.entangledLanes, t !== 0) for (e = e.entanglements, t &= r; 0 < t; ) n = 31 - Oe(t), l = 1 << n, r |= e[n], t &= ~l;
  return r;
}
function kf(e, t) {
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
function Sf(e, t) {
  for (var n = e.suspendedLanes, r = e.pingedLanes, l = e.expirationTimes, i = e.pendingLanes; 0 < i; ) {
    var o = 31 - Oe(i), u = 1 << o, s = l[o];
    s === -1 ? (!(u & n) || u & r) && (l[o] = kf(u, t)) : s <= t && (e.expiredLanes |= u), i &= ~u;
  }
}
function vi(e) {
  return e = e.pendingLanes & -1073741825, e !== 0 ? e : e & 1073741824 ? 1073741824 : 0;
}
function Ws() {
  var e = fr;
  return fr <<= 1, !(fr & 4194240) && (fr = 64), e;
}
function Tl(e) {
  for (var t = [], n = 0; 31 > n; n++) t.push(e);
  return t;
}
function er(e, t, n) {
  e.pendingLanes |= t, t !== 536870912 && (e.suspendedLanes = 0, e.pingedLanes = 0), e = e.eventTimes, t = 31 - Oe(t), e[t] = n;
}
function xf(e, t) {
  var n = e.pendingLanes & ~t;
  e.pendingLanes = t, e.suspendedLanes = 0, e.pingedLanes = 0, e.expiredLanes &= t, e.mutableReadLanes &= t, e.entangledLanes &= t, t = e.entanglements;
  var r = e.eventTimes;
  for (e = e.expirationTimes; 0 < n; ) {
    var l = 31 - Oe(n), i = 1 << l;
    t[l] = 0, r[l] = -1, e[l] = -1, n &= ~i;
  }
}
function lo(e, t) {
  var n = e.entangledLanes |= t;
  for (e = e.entanglements; n; ) {
    var r = 31 - Oe(n), l = 1 << r;
    l & t | e[r] & t && (e[r] |= t), n &= ~l;
  }
}
var F = 0;
function Ks(e) {
  return e &= -e, 1 < e ? 4 < e ? e & 268435455 ? 16 : 536870912 : 4 : 1;
}
var Ys, io, Xs, Gs, Zs, gi = !1, pr = [], ut = null, st = null, at = null, An = /* @__PURE__ */ new Map(), Vn = /* @__PURE__ */ new Map(), rt = [], Ef = "mousedown mouseup touchcancel touchend touchstart auxclick dblclick pointercancel pointerdown pointerup dragend dragstart drop compositionend compositionstart keydown keypress keyup input textInput copy cut paste click change contextmenu reset submit".split(" ");
function nu(e, t) {
  switch (e) {
    case "focusin":
    case "focusout":
      ut = null;
      break;
    case "dragenter":
    case "dragleave":
      st = null;
      break;
    case "mouseover":
    case "mouseout":
      at = null;
      break;
    case "pointerover":
    case "pointerout":
      An.delete(t.pointerId);
      break;
    case "gotpointercapture":
    case "lostpointercapture":
      Vn.delete(t.pointerId);
  }
}
function gn(e, t, n, r, l, i) {
  return e === null || e.nativeEvent !== i ? (e = { blockedOn: t, domEventName: n, eventSystemFlags: r, nativeEvent: i, targetContainers: [l] }, t !== null && (t = nr(t), t !== null && io(t)), e) : (e.eventSystemFlags |= r, t = e.targetContainers, l !== null && t.indexOf(l) === -1 && t.push(l), e);
}
function Cf(e, t, n, r, l) {
  switch (t) {
    case "focusin":
      return ut = gn(ut, e, t, n, r, l), !0;
    case "dragenter":
      return st = gn(st, e, t, n, r, l), !0;
    case "mouseover":
      return at = gn(at, e, t, n, r, l), !0;
    case "pointerover":
      var i = l.pointerId;
      return An.set(i, gn(An.get(i) || null, e, t, n, r, l)), !0;
    case "gotpointercapture":
      return i = l.pointerId, Vn.set(i, gn(Vn.get(i) || null, e, t, n, r, l)), !0;
  }
  return !1;
}
function Js(e) {
  var t = Ct(e.target);
  if (t !== null) {
    var n = Dt(t);
    if (n !== null) {
      if (t = n.tag, t === 13) {
        if (t = As(n), t !== null) {
          e.blockedOn = t, Zs(e.priority, function() {
            Xs(n);
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
function Nr(e) {
  if (e.blockedOn !== null) return !1;
  for (var t = e.targetContainers; 0 < t.length; ) {
    var n = yi(e.domEventName, e.eventSystemFlags, t[0], e.nativeEvent);
    if (n === null) {
      n = e.nativeEvent;
      var r = new n.constructor(n.type, n);
      di = r, n.target.dispatchEvent(r), di = null;
    } else return t = nr(n), t !== null && io(t), e.blockedOn = n, !1;
    t.shift();
  }
  return !0;
}
function ru(e, t, n) {
  Nr(e) && n.delete(t);
}
function _f() {
  gi = !1, ut !== null && Nr(ut) && (ut = null), st !== null && Nr(st) && (st = null), at !== null && Nr(at) && (at = null), An.forEach(ru), Vn.forEach(ru);
}
function yn(e, t) {
  e.blockedOn === t && (e.blockedOn = null, gi || (gi = !0, xe.unstable_scheduleCallback(xe.unstable_NormalPriority, _f)));
}
function $n(e) {
  function t(l) {
    return yn(l, e);
  }
  if (0 < pr.length) {
    yn(pr[0], e);
    for (var n = 1; n < pr.length; n++) {
      var r = pr[n];
      r.blockedOn === e && (r.blockedOn = null);
    }
  }
  for (ut !== null && yn(ut, e), st !== null && yn(st, e), at !== null && yn(at, e), An.forEach(t), Vn.forEach(t), n = 0; n < rt.length; n++) r = rt[n], r.blockedOn === e && (r.blockedOn = null);
  for (; 0 < rt.length && (n = rt[0], n.blockedOn === null); ) Js(n), n.blockedOn === null && rt.shift();
}
var qt = be.ReactCurrentBatchConfig, Br = !0;
function Nf(e, t, n, r) {
  var l = F, i = qt.transition;
  qt.transition = null;
  try {
    F = 1, oo(e, t, n, r);
  } finally {
    F = l, qt.transition = i;
  }
}
function Pf(e, t, n, r) {
  var l = F, i = qt.transition;
  qt.transition = null;
  try {
    F = 4, oo(e, t, n, r);
  } finally {
    F = l, qt.transition = i;
  }
}
function oo(e, t, n, r) {
  if (Br) {
    var l = yi(e, t, n, r);
    if (l === null) Vl(e, t, r, Hr, n), nu(e, r);
    else if (Cf(l, e, t, n, r)) r.stopPropagation();
    else if (nu(e, r), t & 4 && -1 < Ef.indexOf(e)) {
      for (; l !== null; ) {
        var i = nr(l);
        if (i !== null && Ys(i), i = yi(e, t, n, r), i === null && Vl(e, t, r, Hr, n), i === l) break;
        l = i;
      }
      l !== null && r.stopPropagation();
    } else Vl(e, t, r, null, n);
  }
}
var Hr = null;
function yi(e, t, n, r) {
  if (Hr = null, e = no(r), e = Ct(e), e !== null) if (t = Dt(e), t === null) e = null;
  else if (n = t.tag, n === 13) {
    if (e = As(t), e !== null) return e;
    e = null;
  } else if (n === 3) {
    if (t.stateNode.current.memoizedState.isDehydrated) return t.tag === 3 ? t.stateNode.containerInfo : null;
    e = null;
  } else t !== e && (e = null);
  return Hr = e, null;
}
function qs(e) {
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
      switch (mf()) {
        case ro:
          return 1;
        case Hs:
          return 4;
        case Vr:
        case hf:
          return 16;
        case Qs:
          return 536870912;
        default:
          return 16;
      }
    default:
      return 16;
  }
}
var it = null, uo = null, Pr = null;
function bs() {
  if (Pr) return Pr;
  var e, t = uo, n = t.length, r, l = "value" in it ? it.value : it.textContent, i = l.length;
  for (e = 0; e < n && t[e] === l[e]; e++) ;
  var o = n - e;
  for (r = 1; r <= o && t[n - r] === l[i - r]; r++) ;
  return Pr = l.slice(e, 1 < r ? 1 - r : void 0);
}
function jr(e) {
  var t = e.keyCode;
  return "charCode" in e ? (e = e.charCode, e === 0 && t === 13 && (e = 13)) : e = t, e === 10 && (e = 13), 32 <= e || e === 13 ? e : 0;
}
function mr() {
  return !0;
}
function lu() {
  return !1;
}
function Ce(e) {
  function t(n, r, l, i, o) {
    this._reactName = n, this._targetInst = l, this.type = r, this.nativeEvent = i, this.target = o, this.currentTarget = null;
    for (var u in e) e.hasOwnProperty(u) && (n = e[u], this[u] = n ? n(i) : i[u]);
    return this.isDefaultPrevented = (i.defaultPrevented != null ? i.defaultPrevented : i.returnValue === !1) ? mr : lu, this.isPropagationStopped = lu, this;
  }
  return K(t.prototype, { preventDefault: function() {
    this.defaultPrevented = !0;
    var n = this.nativeEvent;
    n && (n.preventDefault ? n.preventDefault() : typeof n.returnValue != "unknown" && (n.returnValue = !1), this.isDefaultPrevented = mr);
  }, stopPropagation: function() {
    var n = this.nativeEvent;
    n && (n.stopPropagation ? n.stopPropagation() : typeof n.cancelBubble != "unknown" && (n.cancelBubble = !0), this.isPropagationStopped = mr);
  }, persist: function() {
  }, isPersistent: mr }), t;
}
var cn = { eventPhase: 0, bubbles: 0, cancelable: 0, timeStamp: function(e) {
  return e.timeStamp || Date.now();
}, defaultPrevented: 0, isTrusted: 0 }, so = Ce(cn), tr = K({}, cn, { view: 0, detail: 0 }), jf = Ce(tr), zl, Rl, wn, al = K({}, tr, { screenX: 0, screenY: 0, clientX: 0, clientY: 0, pageX: 0, pageY: 0, ctrlKey: 0, shiftKey: 0, altKey: 0, metaKey: 0, getModifierState: ao, button: 0, buttons: 0, relatedTarget: function(e) {
  return e.relatedTarget === void 0 ? e.fromElement === e.srcElement ? e.toElement : e.fromElement : e.relatedTarget;
}, movementX: function(e) {
  return "movementX" in e ? e.movementX : (e !== wn && (wn && e.type === "mousemove" ? (zl = e.screenX - wn.screenX, Rl = e.screenY - wn.screenY) : Rl = zl = 0, wn = e), zl);
}, movementY: function(e) {
  return "movementY" in e ? e.movementY : Rl;
} }), iu = Ce(al), Lf = K({}, al, { dataTransfer: 0 }), Tf = Ce(Lf), zf = K({}, tr, { relatedTarget: 0 }), Ml = Ce(zf), Rf = K({}, cn, { animationName: 0, elapsedTime: 0, pseudoElement: 0 }), Mf = Ce(Rf), If = K({}, cn, { clipboardData: function(e) {
  return "clipboardData" in e ? e.clipboardData : window.clipboardData;
} }), Df = Ce(If), Of = K({}, cn, { data: 0 }), ou = Ce(Of), Ff = {
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
}, Uf = {
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
}, Af = { Alt: "altKey", Control: "ctrlKey", Meta: "metaKey", Shift: "shiftKey" };
function Vf(e) {
  var t = this.nativeEvent;
  return t.getModifierState ? t.getModifierState(e) : (e = Af[e]) ? !!t[e] : !1;
}
function ao() {
  return Vf;
}
var $f = K({}, tr, { key: function(e) {
  if (e.key) {
    var t = Ff[e.key] || e.key;
    if (t !== "Unidentified") return t;
  }
  return e.type === "keypress" ? (e = jr(e), e === 13 ? "Enter" : String.fromCharCode(e)) : e.type === "keydown" || e.type === "keyup" ? Uf[e.keyCode] || "Unidentified" : "";
}, code: 0, location: 0, ctrlKey: 0, shiftKey: 0, altKey: 0, metaKey: 0, repeat: 0, locale: 0, getModifierState: ao, charCode: function(e) {
  return e.type === "keypress" ? jr(e) : 0;
}, keyCode: function(e) {
  return e.type === "keydown" || e.type === "keyup" ? e.keyCode : 0;
}, which: function(e) {
  return e.type === "keypress" ? jr(e) : e.type === "keydown" || e.type === "keyup" ? e.keyCode : 0;
} }), Bf = Ce($f), Hf = K({}, al, { pointerId: 0, width: 0, height: 0, pressure: 0, tangentialPressure: 0, tiltX: 0, tiltY: 0, twist: 0, pointerType: 0, isPrimary: 0 }), uu = Ce(Hf), Qf = K({}, tr, { touches: 0, targetTouches: 0, changedTouches: 0, altKey: 0, metaKey: 0, ctrlKey: 0, shiftKey: 0, getModifierState: ao }), Wf = Ce(Qf), Kf = K({}, cn, { propertyName: 0, elapsedTime: 0, pseudoElement: 0 }), Yf = Ce(Kf), Xf = K({}, al, {
  deltaX: function(e) {
    return "deltaX" in e ? e.deltaX : "wheelDeltaX" in e ? -e.wheelDeltaX : 0;
  },
  deltaY: function(e) {
    return "deltaY" in e ? e.deltaY : "wheelDeltaY" in e ? -e.wheelDeltaY : "wheelDelta" in e ? -e.wheelDelta : 0;
  },
  deltaZ: 0,
  deltaMode: 0
}), Gf = Ce(Xf), Zf = [9, 13, 27, 32], co = Ge && "CompositionEvent" in window, Ln = null;
Ge && "documentMode" in document && (Ln = document.documentMode);
var Jf = Ge && "TextEvent" in window && !Ln, ea = Ge && (!co || Ln && 8 < Ln && 11 >= Ln), su = " ", au = !1;
function ta(e, t) {
  switch (e) {
    case "keyup":
      return Zf.indexOf(t.keyCode) !== -1;
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
function na(e) {
  return e = e.detail, typeof e == "object" && "data" in e ? e.data : null;
}
var At = !1;
function qf(e, t) {
  switch (e) {
    case "compositionend":
      return na(t);
    case "keypress":
      return t.which !== 32 ? null : (au = !0, su);
    case "textInput":
      return e = t.data, e === su && au ? null : e;
    default:
      return null;
  }
}
function bf(e, t) {
  if (At) return e === "compositionend" || !co && ta(e, t) ? (e = bs(), Pr = uo = it = null, At = !1, e) : null;
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
      return ea && t.locale !== "ko" ? null : t.data;
    default:
      return null;
  }
}
var ed = { color: !0, date: !0, datetime: !0, "datetime-local": !0, email: !0, month: !0, number: !0, password: !0, range: !0, search: !0, tel: !0, text: !0, time: !0, url: !0, week: !0 };
function cu(e) {
  var t = e && e.nodeName && e.nodeName.toLowerCase();
  return t === "input" ? !!ed[e.type] : t === "textarea";
}
function ra(e, t, n, r) {
  Is(r), t = Qr(t, "onChange"), 0 < t.length && (n = new so("onChange", "change", null, n, r), e.push({ event: n, listeners: t }));
}
var Tn = null, Bn = null;
function td(e) {
  ma(e, 0);
}
function cl(e) {
  var t = Bt(e);
  if (Ps(t)) return e;
}
function nd(e, t) {
  if (e === "change") return t;
}
var la = !1;
if (Ge) {
  var Il;
  if (Ge) {
    var Dl = "oninput" in document;
    if (!Dl) {
      var fu = document.createElement("div");
      fu.setAttribute("oninput", "return;"), Dl = typeof fu.oninput == "function";
    }
    Il = Dl;
  } else Il = !1;
  la = Il && (!document.documentMode || 9 < document.documentMode);
}
function du() {
  Tn && (Tn.detachEvent("onpropertychange", ia), Bn = Tn = null);
}
function ia(e) {
  if (e.propertyName === "value" && cl(Bn)) {
    var t = [];
    ra(t, Bn, e, no(e)), Us(td, t);
  }
}
function rd(e, t, n) {
  e === "focusin" ? (du(), Tn = t, Bn = n, Tn.attachEvent("onpropertychange", ia)) : e === "focusout" && du();
}
function ld(e) {
  if (e === "selectionchange" || e === "keyup" || e === "keydown") return cl(Bn);
}
function id(e, t) {
  if (e === "click") return cl(t);
}
function od(e, t) {
  if (e === "input" || e === "change") return cl(t);
}
function ud(e, t) {
  return e === t && (e !== 0 || 1 / e === 1 / t) || e !== e && t !== t;
}
var Ue = typeof Object.is == "function" ? Object.is : ud;
function Hn(e, t) {
  if (Ue(e, t)) return !0;
  if (typeof e != "object" || e === null || typeof t != "object" || t === null) return !1;
  var n = Object.keys(e), r = Object.keys(t);
  if (n.length !== r.length) return !1;
  for (r = 0; r < n.length; r++) {
    var l = n[r];
    if (!ei.call(t, l) || !Ue(e[l], t[l])) return !1;
  }
  return !0;
}
function pu(e) {
  for (; e && e.firstChild; ) e = e.firstChild;
  return e;
}
function mu(e, t) {
  var n = pu(e);
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
    n = pu(n);
  }
}
function oa(e, t) {
  return e && t ? e === t ? !0 : e && e.nodeType === 3 ? !1 : t && t.nodeType === 3 ? oa(e, t.parentNode) : "contains" in e ? e.contains(t) : e.compareDocumentPosition ? !!(e.compareDocumentPosition(t) & 16) : !1 : !1;
}
function ua() {
  for (var e = window, t = Fr(); t instanceof e.HTMLIFrameElement; ) {
    try {
      var n = typeof t.contentWindow.location.href == "string";
    } catch {
      n = !1;
    }
    if (n) e = t.contentWindow;
    else break;
    t = Fr(e.document);
  }
  return t;
}
function fo(e) {
  var t = e && e.nodeName && e.nodeName.toLowerCase();
  return t && (t === "input" && (e.type === "text" || e.type === "search" || e.type === "tel" || e.type === "url" || e.type === "password") || t === "textarea" || e.contentEditable === "true");
}
function sd(e) {
  var t = ua(), n = e.focusedElem, r = e.selectionRange;
  if (t !== n && n && n.ownerDocument && oa(n.ownerDocument.documentElement, n)) {
    if (r !== null && fo(n)) {
      if (t = r.start, e = r.end, e === void 0 && (e = t), "selectionStart" in n) n.selectionStart = t, n.selectionEnd = Math.min(e, n.value.length);
      else if (e = (t = n.ownerDocument || document) && t.defaultView || window, e.getSelection) {
        e = e.getSelection();
        var l = n.textContent.length, i = Math.min(r.start, l);
        r = r.end === void 0 ? i : Math.min(r.end, l), !e.extend && i > r && (l = r, r = i, i = l), l = mu(n, i);
        var o = mu(
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
var ad = Ge && "documentMode" in document && 11 >= document.documentMode, Vt = null, wi = null, zn = null, ki = !1;
function hu(e, t, n) {
  var r = n.window === n ? n.document : n.nodeType === 9 ? n : n.ownerDocument;
  ki || Vt == null || Vt !== Fr(r) || (r = Vt, "selectionStart" in r && fo(r) ? r = { start: r.selectionStart, end: r.selectionEnd } : (r = (r.ownerDocument && r.ownerDocument.defaultView || window).getSelection(), r = { anchorNode: r.anchorNode, anchorOffset: r.anchorOffset, focusNode: r.focusNode, focusOffset: r.focusOffset }), zn && Hn(zn, r) || (zn = r, r = Qr(wi, "onSelect"), 0 < r.length && (t = new so("onSelect", "select", null, t, n), e.push({ event: t, listeners: r }), t.target = Vt)));
}
function hr(e, t) {
  var n = {};
  return n[e.toLowerCase()] = t.toLowerCase(), n["Webkit" + e] = "webkit" + t, n["Moz" + e] = "moz" + t, n;
}
var $t = { animationend: hr("Animation", "AnimationEnd"), animationiteration: hr("Animation", "AnimationIteration"), animationstart: hr("Animation", "AnimationStart"), transitionend: hr("Transition", "TransitionEnd") }, Ol = {}, sa = {};
Ge && (sa = document.createElement("div").style, "AnimationEvent" in window || (delete $t.animationend.animation, delete $t.animationiteration.animation, delete $t.animationstart.animation), "TransitionEvent" in window || delete $t.transitionend.transition);
function fl(e) {
  if (Ol[e]) return Ol[e];
  if (!$t[e]) return e;
  var t = $t[e], n;
  for (n in t) if (t.hasOwnProperty(n) && n in sa) return Ol[e] = t[n];
  return e;
}
var aa = fl("animationend"), ca = fl("animationiteration"), fa = fl("animationstart"), da = fl("transitionend"), pa = /* @__PURE__ */ new Map(), vu = "abort auxClick cancel canPlay canPlayThrough click close contextMenu copy cut drag dragEnd dragEnter dragExit dragLeave dragOver dragStart drop durationChange emptied encrypted ended error gotPointerCapture input invalid keyDown keyPress keyUp load loadedData loadedMetadata loadStart lostPointerCapture mouseDown mouseMove mouseOut mouseOver mouseUp paste pause play playing pointerCancel pointerDown pointerMove pointerOut pointerOver pointerUp progress rateChange reset resize seeked seeking stalled submit suspend timeUpdate touchCancel touchEnd touchStart volumeChange scroll toggle touchMove waiting wheel".split(" ");
function gt(e, t) {
  pa.set(e, t), It(t, [e]);
}
for (var Fl = 0; Fl < vu.length; Fl++) {
  var Ul = vu[Fl], cd = Ul.toLowerCase(), fd = Ul[0].toUpperCase() + Ul.slice(1);
  gt(cd, "on" + fd);
}
gt(aa, "onAnimationEnd");
gt(ca, "onAnimationIteration");
gt(fa, "onAnimationStart");
gt("dblclick", "onDoubleClick");
gt("focusin", "onFocus");
gt("focusout", "onBlur");
gt(da, "onTransitionEnd");
tn("onMouseEnter", ["mouseout", "mouseover"]);
tn("onMouseLeave", ["mouseout", "mouseover"]);
tn("onPointerEnter", ["pointerout", "pointerover"]);
tn("onPointerLeave", ["pointerout", "pointerover"]);
It("onChange", "change click focusin focusout input keydown keyup selectionchange".split(" "));
It("onSelect", "focusout contextmenu dragend focusin keydown keyup mousedown mouseup selectionchange".split(" "));
It("onBeforeInput", ["compositionend", "keypress", "textInput", "paste"]);
It("onCompositionEnd", "compositionend focusout keydown keypress keyup mousedown".split(" "));
It("onCompositionStart", "compositionstart focusout keydown keypress keyup mousedown".split(" "));
It("onCompositionUpdate", "compositionupdate focusout keydown keypress keyup mousedown".split(" "));
var Nn = "abort canplay canplaythrough durationchange emptied encrypted ended error loadeddata loadedmetadata loadstart pause play playing progress ratechange resize seeked seeking stalled suspend timeupdate volumechange waiting".split(" "), dd = new Set("cancel close invalid load scroll toggle".split(" ").concat(Nn));
function gu(e, t, n) {
  var r = e.type || "unknown-event";
  e.currentTarget = n, cf(r, t, void 0, e), e.currentTarget = null;
}
function ma(e, t) {
  t = (t & 4) !== 0;
  for (var n = 0; n < e.length; n++) {
    var r = e[n], l = r.event;
    r = r.listeners;
    e: {
      var i = void 0;
      if (t) for (var o = r.length - 1; 0 <= o; o--) {
        var u = r[o], s = u.instance, c = u.currentTarget;
        if (u = u.listener, s !== i && l.isPropagationStopped()) break e;
        gu(l, u, c), i = s;
      }
      else for (o = 0; o < r.length; o++) {
        if (u = r[o], s = u.instance, c = u.currentTarget, u = u.listener, s !== i && l.isPropagationStopped()) break e;
        gu(l, u, c), i = s;
      }
    }
  }
  if (Ar) throw e = hi, Ar = !1, hi = null, e;
}
function V(e, t) {
  var n = t[_i];
  n === void 0 && (n = t[_i] = /* @__PURE__ */ new Set());
  var r = e + "__bubble";
  n.has(r) || (ha(t, e, 2, !1), n.add(r));
}
function Al(e, t, n) {
  var r = 0;
  t && (r |= 4), ha(n, e, r, t);
}
var vr = "_reactListening" + Math.random().toString(36).slice(2);
function Qn(e) {
  if (!e[vr]) {
    e[vr] = !0, xs.forEach(function(n) {
      n !== "selectionchange" && (dd.has(n) || Al(n, !1, e), Al(n, !0, e));
    });
    var t = e.nodeType === 9 ? e : e.ownerDocument;
    t === null || t[vr] || (t[vr] = !0, Al("selectionchange", !1, t));
  }
}
function ha(e, t, n, r) {
  switch (qs(t)) {
    case 1:
      var l = Nf;
      break;
    case 4:
      l = Pf;
      break;
    default:
      l = oo;
  }
  n = l.bind(null, t, n, e), l = void 0, !mi || t !== "touchstart" && t !== "touchmove" && t !== "wheel" || (l = !0), r ? l !== void 0 ? e.addEventListener(t, n, { capture: !0, passive: l }) : e.addEventListener(t, n, !0) : l !== void 0 ? e.addEventListener(t, n, { passive: l }) : e.addEventListener(t, n, !1);
}
function Vl(e, t, n, r, l) {
  var i = r;
  if (!(t & 1) && !(t & 2) && r !== null) e: for (; ; ) {
    if (r === null) return;
    var o = r.tag;
    if (o === 3 || o === 4) {
      var u = r.stateNode.containerInfo;
      if (u === l || u.nodeType === 8 && u.parentNode === l) break;
      if (o === 4) for (o = r.return; o !== null; ) {
        var s = o.tag;
        if ((s === 3 || s === 4) && (s = o.stateNode.containerInfo, s === l || s.nodeType === 8 && s.parentNode === l)) return;
        o = o.return;
      }
      for (; u !== null; ) {
        if (o = Ct(u), o === null) return;
        if (s = o.tag, s === 5 || s === 6) {
          r = i = o;
          continue e;
        }
        u = u.parentNode;
      }
    }
    r = r.return;
  }
  Us(function() {
    var c = i, h = no(n), v = [];
    e: {
      var f = pa.get(e);
      if (f !== void 0) {
        var w = so, k = e;
        switch (e) {
          case "keypress":
            if (jr(n) === 0) break e;
          case "keydown":
          case "keyup":
            w = Bf;
            break;
          case "focusin":
            k = "focus", w = Ml;
            break;
          case "focusout":
            k = "blur", w = Ml;
            break;
          case "beforeblur":
          case "afterblur":
            w = Ml;
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
            w = iu;
            break;
          case "drag":
          case "dragend":
          case "dragenter":
          case "dragexit":
          case "dragleave":
          case "dragover":
          case "dragstart":
          case "drop":
            w = Tf;
            break;
          case "touchcancel":
          case "touchend":
          case "touchmove":
          case "touchstart":
            w = Wf;
            break;
          case aa:
          case ca:
          case fa:
            w = Mf;
            break;
          case da:
            w = Yf;
            break;
          case "scroll":
            w = jf;
            break;
          case "wheel":
            w = Gf;
            break;
          case "copy":
          case "cut":
          case "paste":
            w = Df;
            break;
          case "gotpointercapture":
          case "lostpointercapture":
          case "pointercancel":
          case "pointerdown":
          case "pointermove":
          case "pointerout":
          case "pointerover":
          case "pointerup":
            w = uu;
        }
        var E = (t & 4) !== 0, M = !E && e === "scroll", d = E ? f !== null ? f + "Capture" : null : f;
        E = [];
        for (var a = c, p; a !== null; ) {
          p = a;
          var g = p.stateNode;
          if (p.tag === 5 && g !== null && (p = g, d !== null && (g = Un(a, d), g != null && E.push(Wn(a, g, p)))), M) break;
          a = a.return;
        }
        0 < E.length && (f = new w(f, k, null, n, h), v.push({ event: f, listeners: E }));
      }
    }
    if (!(t & 7)) {
      e: {
        if (f = e === "mouseover" || e === "pointerover", w = e === "mouseout" || e === "pointerout", f && n !== di && (k = n.relatedTarget || n.fromElement) && (Ct(k) || k[Ze])) break e;
        if ((w || f) && (f = h.window === h ? h : (f = h.ownerDocument) ? f.defaultView || f.parentWindow : window, w ? (k = n.relatedTarget || n.toElement, w = c, k = k ? Ct(k) : null, k !== null && (M = Dt(k), k !== M || k.tag !== 5 && k.tag !== 6) && (k = null)) : (w = null, k = c), w !== k)) {
          if (E = iu, g = "onMouseLeave", d = "onMouseEnter", a = "mouse", (e === "pointerout" || e === "pointerover") && (E = uu, g = "onPointerLeave", d = "onPointerEnter", a = "pointer"), M = w == null ? f : Bt(w), p = k == null ? f : Bt(k), f = new E(g, a + "leave", w, n, h), f.target = M, f.relatedTarget = p, g = null, Ct(h) === c && (E = new E(d, a + "enter", k, n, h), E.target = p, E.relatedTarget = M, g = E), M = g, w && k) t: {
            for (E = w, d = k, a = 0, p = E; p; p = Ot(p)) a++;
            for (p = 0, g = d; g; g = Ot(g)) p++;
            for (; 0 < a - p; ) E = Ot(E), a--;
            for (; 0 < p - a; ) d = Ot(d), p--;
            for (; a--; ) {
              if (E === d || d !== null && E === d.alternate) break t;
              E = Ot(E), d = Ot(d);
            }
            E = null;
          }
          else E = null;
          w !== null && yu(v, f, w, E, !1), k !== null && M !== null && yu(v, M, k, E, !0);
        }
      }
      e: {
        if (f = c ? Bt(c) : window, w = f.nodeName && f.nodeName.toLowerCase(), w === "select" || w === "input" && f.type === "file") var C = nd;
        else if (cu(f)) if (la) C = od;
        else {
          C = ld;
          var _ = rd;
        }
        else (w = f.nodeName) && w.toLowerCase() === "input" && (f.type === "checkbox" || f.type === "radio") && (C = id);
        if (C && (C = C(e, c))) {
          ra(v, C, n, h);
          break e;
        }
        _ && _(e, f, c), e === "focusout" && (_ = f._wrapperState) && _.controlled && f.type === "number" && ui(f, "number", f.value);
      }
      switch (_ = c ? Bt(c) : window, e) {
        case "focusin":
          (cu(_) || _.contentEditable === "true") && (Vt = _, wi = c, zn = null);
          break;
        case "focusout":
          zn = wi = Vt = null;
          break;
        case "mousedown":
          ki = !0;
          break;
        case "contextmenu":
        case "mouseup":
        case "dragend":
          ki = !1, hu(v, n, h);
          break;
        case "selectionchange":
          if (ad) break;
        case "keydown":
        case "keyup":
          hu(v, n, h);
      }
      var x;
      if (co) e: {
        switch (e) {
          case "compositionstart":
            var N = "onCompositionStart";
            break e;
          case "compositionend":
            N = "onCompositionEnd";
            break e;
          case "compositionupdate":
            N = "onCompositionUpdate";
            break e;
        }
        N = void 0;
      }
      else At ? ta(e, n) && (N = "onCompositionEnd") : e === "keydown" && n.keyCode === 229 && (N = "onCompositionStart");
      N && (ea && n.locale !== "ko" && (At || N !== "onCompositionStart" ? N === "onCompositionEnd" && At && (x = bs()) : (it = h, uo = "value" in it ? it.value : it.textContent, At = !0)), _ = Qr(c, N), 0 < _.length && (N = new ou(N, e, null, n, h), v.push({ event: N, listeners: _ }), x ? N.data = x : (x = na(n), x !== null && (N.data = x)))), (x = Jf ? qf(e, n) : bf(e, n)) && (c = Qr(c, "onBeforeInput"), 0 < c.length && (h = new ou("onBeforeInput", "beforeinput", null, n, h), v.push({ event: h, listeners: c }), h.data = x));
    }
    ma(v, t);
  });
}
function Wn(e, t, n) {
  return { instance: e, listener: t, currentTarget: n };
}
function Qr(e, t) {
  for (var n = t + "Capture", r = []; e !== null; ) {
    var l = e, i = l.stateNode;
    l.tag === 5 && i !== null && (l = i, i = Un(e, n), i != null && r.unshift(Wn(e, i, l)), i = Un(e, t), i != null && r.push(Wn(e, i, l))), e = e.return;
  }
  return r;
}
function Ot(e) {
  if (e === null) return null;
  do
    e = e.return;
  while (e && e.tag !== 5);
  return e || null;
}
function yu(e, t, n, r, l) {
  for (var i = t._reactName, o = []; n !== null && n !== r; ) {
    var u = n, s = u.alternate, c = u.stateNode;
    if (s !== null && s === r) break;
    u.tag === 5 && c !== null && (u = c, l ? (s = Un(n, i), s != null && o.unshift(Wn(n, s, u))) : l || (s = Un(n, i), s != null && o.push(Wn(n, s, u)))), n = n.return;
  }
  o.length !== 0 && e.push({ event: t, listeners: o });
}
var pd = /\r\n?/g, md = /\u0000|\uFFFD/g;
function wu(e) {
  return (typeof e == "string" ? e : "" + e).replace(pd, `
`).replace(md, "");
}
function gr(e, t, n) {
  if (t = wu(t), wu(e) !== t && n) throw Error(S(425));
}
function Wr() {
}
var Si = null, xi = null;
function Ei(e, t) {
  return e === "textarea" || e === "noscript" || typeof t.children == "string" || typeof t.children == "number" || typeof t.dangerouslySetInnerHTML == "object" && t.dangerouslySetInnerHTML !== null && t.dangerouslySetInnerHTML.__html != null;
}
var Ci = typeof setTimeout == "function" ? setTimeout : void 0, hd = typeof clearTimeout == "function" ? clearTimeout : void 0, ku = typeof Promise == "function" ? Promise : void 0, vd = typeof queueMicrotask == "function" ? queueMicrotask : typeof ku < "u" ? function(e) {
  return ku.resolve(null).then(e).catch(gd);
} : Ci;
function gd(e) {
  setTimeout(function() {
    throw e;
  });
}
function $l(e, t) {
  var n = t, r = 0;
  do {
    var l = n.nextSibling;
    if (e.removeChild(n), l && l.nodeType === 8) if (n = l.data, n === "/$") {
      if (r === 0) {
        e.removeChild(l), $n(t);
        return;
      }
      r--;
    } else n !== "$" && n !== "$?" && n !== "$!" || r++;
    n = l;
  } while (n);
  $n(t);
}
function ct(e) {
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
function Su(e) {
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
var fn = Math.random().toString(36).slice(2), $e = "__reactFiber$" + fn, Kn = "__reactProps$" + fn, Ze = "__reactContainer$" + fn, _i = "__reactEvents$" + fn, yd = "__reactListeners$" + fn, wd = "__reactHandles$" + fn;
function Ct(e) {
  var t = e[$e];
  if (t) return t;
  for (var n = e.parentNode; n; ) {
    if (t = n[Ze] || n[$e]) {
      if (n = t.alternate, t.child !== null || n !== null && n.child !== null) for (e = Su(e); e !== null; ) {
        if (n = e[$e]) return n;
        e = Su(e);
      }
      return t;
    }
    e = n, n = e.parentNode;
  }
  return null;
}
function nr(e) {
  return e = e[$e] || e[Ze], !e || e.tag !== 5 && e.tag !== 6 && e.tag !== 13 && e.tag !== 3 ? null : e;
}
function Bt(e) {
  if (e.tag === 5 || e.tag === 6) return e.stateNode;
  throw Error(S(33));
}
function dl(e) {
  return e[Kn] || null;
}
var Ni = [], Ht = -1;
function yt(e) {
  return { current: e };
}
function $(e) {
  0 > Ht || (e.current = Ni[Ht], Ni[Ht] = null, Ht--);
}
function U(e, t) {
  Ht++, Ni[Ht] = e.current, e.current = t;
}
var vt = {}, se = yt(vt), ve = yt(!1), Lt = vt;
function nn(e, t) {
  var n = e.type.contextTypes;
  if (!n) return vt;
  var r = e.stateNode;
  if (r && r.__reactInternalMemoizedUnmaskedChildContext === t) return r.__reactInternalMemoizedMaskedChildContext;
  var l = {}, i;
  for (i in n) l[i] = t[i];
  return r && (e = e.stateNode, e.__reactInternalMemoizedUnmaskedChildContext = t, e.__reactInternalMemoizedMaskedChildContext = l), l;
}
function ge(e) {
  return e = e.childContextTypes, e != null;
}
function Kr() {
  $(ve), $(se);
}
function xu(e, t, n) {
  if (se.current !== vt) throw Error(S(168));
  U(se, t), U(ve, n);
}
function va(e, t, n) {
  var r = e.stateNode;
  if (t = t.childContextTypes, typeof r.getChildContext != "function") return n;
  r = r.getChildContext();
  for (var l in r) if (!(l in t)) throw Error(S(108, nf(e) || "Unknown", l));
  return K({}, n, r);
}
function Yr(e) {
  return e = (e = e.stateNode) && e.__reactInternalMemoizedMergedChildContext || vt, Lt = se.current, U(se, e), U(ve, ve.current), !0;
}
function Eu(e, t, n) {
  var r = e.stateNode;
  if (!r) throw Error(S(169));
  n ? (e = va(e, t, Lt), r.__reactInternalMemoizedMergedChildContext = e, $(ve), $(se), U(se, e)) : $(ve), U(ve, n);
}
var We = null, pl = !1, Bl = !1;
function ga(e) {
  We === null ? We = [e] : We.push(e);
}
function kd(e) {
  pl = !0, ga(e);
}
function wt() {
  if (!Bl && We !== null) {
    Bl = !0;
    var e = 0, t = F;
    try {
      var n = We;
      for (F = 1; e < n.length; e++) {
        var r = n[e];
        do
          r = r(!0);
        while (r !== null);
      }
      We = null, pl = !1;
    } catch (l) {
      throw We !== null && (We = We.slice(e + 1)), Bs(ro, wt), l;
    } finally {
      F = t, Bl = !1;
    }
  }
  return null;
}
var Qt = [], Wt = 0, Xr = null, Gr = 0, Ne = [], Pe = 0, Tt = null, Ke = 1, Ye = "";
function xt(e, t) {
  Qt[Wt++] = Gr, Qt[Wt++] = Xr, Xr = e, Gr = t;
}
function ya(e, t, n) {
  Ne[Pe++] = Ke, Ne[Pe++] = Ye, Ne[Pe++] = Tt, Tt = e;
  var r = Ke;
  e = Ye;
  var l = 32 - Oe(r) - 1;
  r &= ~(1 << l), n += 1;
  var i = 32 - Oe(t) + l;
  if (30 < i) {
    var o = l - l % 5;
    i = (r & (1 << o) - 1).toString(32), r >>= o, l -= o, Ke = 1 << 32 - Oe(t) + l | n << l | r, Ye = i + e;
  } else Ke = 1 << i | n << l | r, Ye = e;
}
function po(e) {
  e.return !== null && (xt(e, 1), ya(e, 1, 0));
}
function mo(e) {
  for (; e === Xr; ) Xr = Qt[--Wt], Qt[Wt] = null, Gr = Qt[--Wt], Qt[Wt] = null;
  for (; e === Tt; ) Tt = Ne[--Pe], Ne[Pe] = null, Ye = Ne[--Pe], Ne[Pe] = null, Ke = Ne[--Pe], Ne[Pe] = null;
}
var Se = null, ke = null, H = !1, De = null;
function wa(e, t) {
  var n = je(5, null, null, 0);
  n.elementType = "DELETED", n.stateNode = t, n.return = e, t = e.deletions, t === null ? (e.deletions = [n], e.flags |= 16) : t.push(n);
}
function Cu(e, t) {
  switch (e.tag) {
    case 5:
      var n = e.type;
      return t = t.nodeType !== 1 || n.toLowerCase() !== t.nodeName.toLowerCase() ? null : t, t !== null ? (e.stateNode = t, Se = e, ke = ct(t.firstChild), !0) : !1;
    case 6:
      return t = e.pendingProps === "" || t.nodeType !== 3 ? null : t, t !== null ? (e.stateNode = t, Se = e, ke = null, !0) : !1;
    case 13:
      return t = t.nodeType !== 8 ? null : t, t !== null ? (n = Tt !== null ? { id: Ke, overflow: Ye } : null, e.memoizedState = { dehydrated: t, treeContext: n, retryLane: 1073741824 }, n = je(18, null, null, 0), n.stateNode = t, n.return = e, e.child = n, Se = e, ke = null, !0) : !1;
    default:
      return !1;
  }
}
function Pi(e) {
  return (e.mode & 1) !== 0 && (e.flags & 128) === 0;
}
function ji(e) {
  if (H) {
    var t = ke;
    if (t) {
      var n = t;
      if (!Cu(e, t)) {
        if (Pi(e)) throw Error(S(418));
        t = ct(n.nextSibling);
        var r = Se;
        t && Cu(e, t) ? wa(r, n) : (e.flags = e.flags & -4097 | 2, H = !1, Se = e);
      }
    } else {
      if (Pi(e)) throw Error(S(418));
      e.flags = e.flags & -4097 | 2, H = !1, Se = e;
    }
  }
}
function _u(e) {
  for (e = e.return; e !== null && e.tag !== 5 && e.tag !== 3 && e.tag !== 13; ) e = e.return;
  Se = e;
}
function yr(e) {
  if (e !== Se) return !1;
  if (!H) return _u(e), H = !0, !1;
  var t;
  if ((t = e.tag !== 3) && !(t = e.tag !== 5) && (t = e.type, t = t !== "head" && t !== "body" && !Ei(e.type, e.memoizedProps)), t && (t = ke)) {
    if (Pi(e)) throw ka(), Error(S(418));
    for (; t; ) wa(e, t), t = ct(t.nextSibling);
  }
  if (_u(e), e.tag === 13) {
    if (e = e.memoizedState, e = e !== null ? e.dehydrated : null, !e) throw Error(S(317));
    e: {
      for (e = e.nextSibling, t = 0; e; ) {
        if (e.nodeType === 8) {
          var n = e.data;
          if (n === "/$") {
            if (t === 0) {
              ke = ct(e.nextSibling);
              break e;
            }
            t--;
          } else n !== "$" && n !== "$!" && n !== "$?" || t++;
        }
        e = e.nextSibling;
      }
      ke = null;
    }
  } else ke = Se ? ct(e.stateNode.nextSibling) : null;
  return !0;
}
function ka() {
  for (var e = ke; e; ) e = ct(e.nextSibling);
}
function rn() {
  ke = Se = null, H = !1;
}
function ho(e) {
  De === null ? De = [e] : De.push(e);
}
var Sd = be.ReactCurrentBatchConfig;
function kn(e, t, n) {
  if (e = n.ref, e !== null && typeof e != "function" && typeof e != "object") {
    if (n._owner) {
      if (n = n._owner, n) {
        if (n.tag !== 1) throw Error(S(309));
        var r = n.stateNode;
      }
      if (!r) throw Error(S(147, e));
      var l = r, i = "" + e;
      return t !== null && t.ref !== null && typeof t.ref == "function" && t.ref._stringRef === i ? t.ref : (t = function(o) {
        var u = l.refs;
        o === null ? delete u[i] : u[i] = o;
      }, t._stringRef = i, t);
    }
    if (typeof e != "string") throw Error(S(284));
    if (!n._owner) throw Error(S(290, e));
  }
  return e;
}
function wr(e, t) {
  throw e = Object.prototype.toString.call(t), Error(S(31, e === "[object Object]" ? "object with keys {" + Object.keys(t).join(", ") + "}" : e));
}
function Nu(e) {
  var t = e._init;
  return t(e._payload);
}
function Sa(e) {
  function t(d, a) {
    if (e) {
      var p = d.deletions;
      p === null ? (d.deletions = [a], d.flags |= 16) : p.push(a);
    }
  }
  function n(d, a) {
    if (!e) return null;
    for (; a !== null; ) t(d, a), a = a.sibling;
    return null;
  }
  function r(d, a) {
    for (d = /* @__PURE__ */ new Map(); a !== null; ) a.key !== null ? d.set(a.key, a) : d.set(a.index, a), a = a.sibling;
    return d;
  }
  function l(d, a) {
    return d = mt(d, a), d.index = 0, d.sibling = null, d;
  }
  function i(d, a, p) {
    return d.index = p, e ? (p = d.alternate, p !== null ? (p = p.index, p < a ? (d.flags |= 2, a) : p) : (d.flags |= 2, a)) : (d.flags |= 1048576, a);
  }
  function o(d) {
    return e && d.alternate === null && (d.flags |= 2), d;
  }
  function u(d, a, p, g) {
    return a === null || a.tag !== 6 ? (a = Gl(p, d.mode, g), a.return = d, a) : (a = l(a, p), a.return = d, a);
  }
  function s(d, a, p, g) {
    var C = p.type;
    return C === Ut ? h(d, a, p.props.children, g, p.key) : a !== null && (a.elementType === C || typeof C == "object" && C !== null && C.$$typeof === tt && Nu(C) === a.type) ? (g = l(a, p.props), g.ref = kn(d, a, p), g.return = d, g) : (g = Dr(p.type, p.key, p.props, null, d.mode, g), g.ref = kn(d, a, p), g.return = d, g);
  }
  function c(d, a, p, g) {
    return a === null || a.tag !== 4 || a.stateNode.containerInfo !== p.containerInfo || a.stateNode.implementation !== p.implementation ? (a = Zl(p, d.mode, g), a.return = d, a) : (a = l(a, p.children || []), a.return = d, a);
  }
  function h(d, a, p, g, C) {
    return a === null || a.tag !== 7 ? (a = jt(p, d.mode, g, C), a.return = d, a) : (a = l(a, p), a.return = d, a);
  }
  function v(d, a, p) {
    if (typeof a == "string" && a !== "" || typeof a == "number") return a = Gl("" + a, d.mode, p), a.return = d, a;
    if (typeof a == "object" && a !== null) {
      switch (a.$$typeof) {
        case sr:
          return p = Dr(a.type, a.key, a.props, null, d.mode, p), p.ref = kn(d, null, a), p.return = d, p;
        case Ft:
          return a = Zl(a, d.mode, p), a.return = d, a;
        case tt:
          var g = a._init;
          return v(d, g(a._payload), p);
      }
      if (Cn(a) || hn(a)) return a = jt(a, d.mode, p, null), a.return = d, a;
      wr(d, a);
    }
    return null;
  }
  function f(d, a, p, g) {
    var C = a !== null ? a.key : null;
    if (typeof p == "string" && p !== "" || typeof p == "number") return C !== null ? null : u(d, a, "" + p, g);
    if (typeof p == "object" && p !== null) {
      switch (p.$$typeof) {
        case sr:
          return p.key === C ? s(d, a, p, g) : null;
        case Ft:
          return p.key === C ? c(d, a, p, g) : null;
        case tt:
          return C = p._init, f(
            d,
            a,
            C(p._payload),
            g
          );
      }
      if (Cn(p) || hn(p)) return C !== null ? null : h(d, a, p, g, null);
      wr(d, p);
    }
    return null;
  }
  function w(d, a, p, g, C) {
    if (typeof g == "string" && g !== "" || typeof g == "number") return d = d.get(p) || null, u(a, d, "" + g, C);
    if (typeof g == "object" && g !== null) {
      switch (g.$$typeof) {
        case sr:
          return d = d.get(g.key === null ? p : g.key) || null, s(a, d, g, C);
        case Ft:
          return d = d.get(g.key === null ? p : g.key) || null, c(a, d, g, C);
        case tt:
          var _ = g._init;
          return w(d, a, p, _(g._payload), C);
      }
      if (Cn(g) || hn(g)) return d = d.get(p) || null, h(a, d, g, C, null);
      wr(a, g);
    }
    return null;
  }
  function k(d, a, p, g) {
    for (var C = null, _ = null, x = a, N = a = 0, I = null; x !== null && N < p.length; N++) {
      x.index > N ? (I = x, x = null) : I = x.sibling;
      var y = f(d, x, p[N], g);
      if (y === null) {
        x === null && (x = I);
        break;
      }
      e && x && y.alternate === null && t(d, x), a = i(y, a, N), _ === null ? C = y : _.sibling = y, _ = y, x = I;
    }
    if (N === p.length) return n(d, x), H && xt(d, N), C;
    if (x === null) {
      for (; N < p.length; N++) x = v(d, p[N], g), x !== null && (a = i(x, a, N), _ === null ? C = x : _.sibling = x, _ = x);
      return H && xt(d, N), C;
    }
    for (x = r(d, x); N < p.length; N++) I = w(x, d, N, p[N], g), I !== null && (e && I.alternate !== null && x.delete(I.key === null ? N : I.key), a = i(I, a, N), _ === null ? C = I : _.sibling = I, _ = I);
    return e && x.forEach(function(P) {
      return t(d, P);
    }), H && xt(d, N), C;
  }
  function E(d, a, p, g) {
    var C = hn(p);
    if (typeof C != "function") throw Error(S(150));
    if (p = C.call(p), p == null) throw Error(S(151));
    for (var _ = C = null, x = a, N = a = 0, I = null, y = p.next(); x !== null && !y.done; N++, y = p.next()) {
      x.index > N ? (I = x, x = null) : I = x.sibling;
      var P = f(d, x, y.value, g);
      if (P === null) {
        x === null && (x = I);
        break;
      }
      e && x && P.alternate === null && t(d, x), a = i(P, a, N), _ === null ? C = P : _.sibling = P, _ = P, x = I;
    }
    if (y.done) return n(
      d,
      x
    ), H && xt(d, N), C;
    if (x === null) {
      for (; !y.done; N++, y = p.next()) y = v(d, y.value, g), y !== null && (a = i(y, a, N), _ === null ? C = y : _.sibling = y, _ = y);
      return H && xt(d, N), C;
    }
    for (x = r(d, x); !y.done; N++, y = p.next()) y = w(x, d, N, y.value, g), y !== null && (e && y.alternate !== null && x.delete(y.key === null ? N : y.key), a = i(y, a, N), _ === null ? C = y : _.sibling = y, _ = y);
    return e && x.forEach(function(A) {
      return t(d, A);
    }), H && xt(d, N), C;
  }
  function M(d, a, p, g) {
    if (typeof p == "object" && p !== null && p.type === Ut && p.key === null && (p = p.props.children), typeof p == "object" && p !== null) {
      switch (p.$$typeof) {
        case sr:
          e: {
            for (var C = p.key, _ = a; _ !== null; ) {
              if (_.key === C) {
                if (C = p.type, C === Ut) {
                  if (_.tag === 7) {
                    n(d, _.sibling), a = l(_, p.props.children), a.return = d, d = a;
                    break e;
                  }
                } else if (_.elementType === C || typeof C == "object" && C !== null && C.$$typeof === tt && Nu(C) === _.type) {
                  n(d, _.sibling), a = l(_, p.props), a.ref = kn(d, _, p), a.return = d, d = a;
                  break e;
                }
                n(d, _);
                break;
              } else t(d, _);
              _ = _.sibling;
            }
            p.type === Ut ? (a = jt(p.props.children, d.mode, g, p.key), a.return = d, d = a) : (g = Dr(p.type, p.key, p.props, null, d.mode, g), g.ref = kn(d, a, p), g.return = d, d = g);
          }
          return o(d);
        case Ft:
          e: {
            for (_ = p.key; a !== null; ) {
              if (a.key === _) if (a.tag === 4 && a.stateNode.containerInfo === p.containerInfo && a.stateNode.implementation === p.implementation) {
                n(d, a.sibling), a = l(a, p.children || []), a.return = d, d = a;
                break e;
              } else {
                n(d, a);
                break;
              }
              else t(d, a);
              a = a.sibling;
            }
            a = Zl(p, d.mode, g), a.return = d, d = a;
          }
          return o(d);
        case tt:
          return _ = p._init, M(d, a, _(p._payload), g);
      }
      if (Cn(p)) return k(d, a, p, g);
      if (hn(p)) return E(d, a, p, g);
      wr(d, p);
    }
    return typeof p == "string" && p !== "" || typeof p == "number" ? (p = "" + p, a !== null && a.tag === 6 ? (n(d, a.sibling), a = l(a, p), a.return = d, d = a) : (n(d, a), a = Gl(p, d.mode, g), a.return = d, d = a), o(d)) : n(d, a);
  }
  return M;
}
var ln = Sa(!0), xa = Sa(!1), Zr = yt(null), Jr = null, Kt = null, vo = null;
function go() {
  vo = Kt = Jr = null;
}
function yo(e) {
  var t = Zr.current;
  $(Zr), e._currentValue = t;
}
function Li(e, t, n) {
  for (; e !== null; ) {
    var r = e.alternate;
    if ((e.childLanes & t) !== t ? (e.childLanes |= t, r !== null && (r.childLanes |= t)) : r !== null && (r.childLanes & t) !== t && (r.childLanes |= t), e === n) break;
    e = e.return;
  }
}
function bt(e, t) {
  Jr = e, vo = Kt = null, e = e.dependencies, e !== null && e.firstContext !== null && (e.lanes & t && (he = !0), e.firstContext = null);
}
function Te(e) {
  var t = e._currentValue;
  if (vo !== e) if (e = { context: e, memoizedValue: t, next: null }, Kt === null) {
    if (Jr === null) throw Error(S(308));
    Kt = e, Jr.dependencies = { lanes: 0, firstContext: e };
  } else Kt = Kt.next = e;
  return t;
}
var _t = null;
function wo(e) {
  _t === null ? _t = [e] : _t.push(e);
}
function Ea(e, t, n, r) {
  var l = t.interleaved;
  return l === null ? (n.next = n, wo(t)) : (n.next = l.next, l.next = n), t.interleaved = n, Je(e, r);
}
function Je(e, t) {
  e.lanes |= t;
  var n = e.alternate;
  for (n !== null && (n.lanes |= t), n = e, e = e.return; e !== null; ) e.childLanes |= t, n = e.alternate, n !== null && (n.childLanes |= t), n = e, e = e.return;
  return n.tag === 3 ? n.stateNode : null;
}
var nt = !1;
function ko(e) {
  e.updateQueue = { baseState: e.memoizedState, firstBaseUpdate: null, lastBaseUpdate: null, shared: { pending: null, interleaved: null, lanes: 0 }, effects: null };
}
function Ca(e, t) {
  e = e.updateQueue, t.updateQueue === e && (t.updateQueue = { baseState: e.baseState, firstBaseUpdate: e.firstBaseUpdate, lastBaseUpdate: e.lastBaseUpdate, shared: e.shared, effects: e.effects });
}
function Xe(e, t) {
  return { eventTime: e, lane: t, tag: 0, payload: null, callback: null, next: null };
}
function ft(e, t, n) {
  var r = e.updateQueue;
  if (r === null) return null;
  if (r = r.shared, O & 2) {
    var l = r.pending;
    return l === null ? t.next = t : (t.next = l.next, l.next = t), r.pending = t, Je(e, n);
  }
  return l = r.interleaved, l === null ? (t.next = t, wo(r)) : (t.next = l.next, l.next = t), r.interleaved = t, Je(e, n);
}
function Lr(e, t, n) {
  if (t = t.updateQueue, t !== null && (t = t.shared, (n & 4194240) !== 0)) {
    var r = t.lanes;
    r &= e.pendingLanes, n |= r, t.lanes = n, lo(e, n);
  }
}
function Pu(e, t) {
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
function qr(e, t, n, r) {
  var l = e.updateQueue;
  nt = !1;
  var i = l.firstBaseUpdate, o = l.lastBaseUpdate, u = l.shared.pending;
  if (u !== null) {
    l.shared.pending = null;
    var s = u, c = s.next;
    s.next = null, o === null ? i = c : o.next = c, o = s;
    var h = e.alternate;
    h !== null && (h = h.updateQueue, u = h.lastBaseUpdate, u !== o && (u === null ? h.firstBaseUpdate = c : u.next = c, h.lastBaseUpdate = s));
  }
  if (i !== null) {
    var v = l.baseState;
    o = 0, h = c = s = null, u = i;
    do {
      var f = u.lane, w = u.eventTime;
      if ((r & f) === f) {
        h !== null && (h = h.next = {
          eventTime: w,
          lane: 0,
          tag: u.tag,
          payload: u.payload,
          callback: u.callback,
          next: null
        });
        e: {
          var k = e, E = u;
          switch (f = t, w = n, E.tag) {
            case 1:
              if (k = E.payload, typeof k == "function") {
                v = k.call(w, v, f);
                break e;
              }
              v = k;
              break e;
            case 3:
              k.flags = k.flags & -65537 | 128;
            case 0:
              if (k = E.payload, f = typeof k == "function" ? k.call(w, v, f) : k, f == null) break e;
              v = K({}, v, f);
              break e;
            case 2:
              nt = !0;
          }
        }
        u.callback !== null && u.lane !== 0 && (e.flags |= 64, f = l.effects, f === null ? l.effects = [u] : f.push(u));
      } else w = { eventTime: w, lane: f, tag: u.tag, payload: u.payload, callback: u.callback, next: null }, h === null ? (c = h = w, s = v) : h = h.next = w, o |= f;
      if (u = u.next, u === null) {
        if (u = l.shared.pending, u === null) break;
        f = u, u = f.next, f.next = null, l.lastBaseUpdate = f, l.shared.pending = null;
      }
    } while (!0);
    if (h === null && (s = v), l.baseState = s, l.firstBaseUpdate = c, l.lastBaseUpdate = h, t = l.shared.interleaved, t !== null) {
      l = t;
      do
        o |= l.lane, l = l.next;
      while (l !== t);
    } else i === null && (l.shared.lanes = 0);
    Rt |= o, e.lanes = o, e.memoizedState = v;
  }
}
function ju(e, t, n) {
  if (e = t.effects, t.effects = null, e !== null) for (t = 0; t < e.length; t++) {
    var r = e[t], l = r.callback;
    if (l !== null) {
      if (r.callback = null, r = n, typeof l != "function") throw Error(S(191, l));
      l.call(r);
    }
  }
}
var rr = {}, He = yt(rr), Yn = yt(rr), Xn = yt(rr);
function Nt(e) {
  if (e === rr) throw Error(S(174));
  return e;
}
function So(e, t) {
  switch (U(Xn, t), U(Yn, e), U(He, rr), e = t.nodeType, e) {
    case 9:
    case 11:
      t = (t = t.documentElement) ? t.namespaceURI : ai(null, "");
      break;
    default:
      e = e === 8 ? t.parentNode : t, t = e.namespaceURI || null, e = e.tagName, t = ai(t, e);
  }
  $(He), U(He, t);
}
function on() {
  $(He), $(Yn), $(Xn);
}
function _a(e) {
  Nt(Xn.current);
  var t = Nt(He.current), n = ai(t, e.type);
  t !== n && (U(Yn, e), U(He, n));
}
function xo(e) {
  Yn.current === e && ($(He), $(Yn));
}
var Q = yt(0);
function br(e) {
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
var Hl = [];
function Eo() {
  for (var e = 0; e < Hl.length; e++) Hl[e]._workInProgressVersionPrimary = null;
  Hl.length = 0;
}
var Tr = be.ReactCurrentDispatcher, Ql = be.ReactCurrentBatchConfig, zt = 0, W = null, J = null, ee = null, el = !1, Rn = !1, Gn = 0, xd = 0;
function ie() {
  throw Error(S(321));
}
function Co(e, t) {
  if (t === null) return !1;
  for (var n = 0; n < t.length && n < e.length; n++) if (!Ue(e[n], t[n])) return !1;
  return !0;
}
function _o(e, t, n, r, l, i) {
  if (zt = i, W = t, t.memoizedState = null, t.updateQueue = null, t.lanes = 0, Tr.current = e === null || e.memoizedState === null ? Nd : Pd, e = n(r, l), Rn) {
    i = 0;
    do {
      if (Rn = !1, Gn = 0, 25 <= i) throw Error(S(301));
      i += 1, ee = J = null, t.updateQueue = null, Tr.current = jd, e = n(r, l);
    } while (Rn);
  }
  if (Tr.current = tl, t = J !== null && J.next !== null, zt = 0, ee = J = W = null, el = !1, t) throw Error(S(300));
  return e;
}
function No() {
  var e = Gn !== 0;
  return Gn = 0, e;
}
function Ve() {
  var e = { memoizedState: null, baseState: null, baseQueue: null, queue: null, next: null };
  return ee === null ? W.memoizedState = ee = e : ee = ee.next = e, ee;
}
function ze() {
  if (J === null) {
    var e = W.alternate;
    e = e !== null ? e.memoizedState : null;
  } else e = J.next;
  var t = ee === null ? W.memoizedState : ee.next;
  if (t !== null) ee = t, J = e;
  else {
    if (e === null) throw Error(S(310));
    J = e, e = { memoizedState: J.memoizedState, baseState: J.baseState, baseQueue: J.baseQueue, queue: J.queue, next: null }, ee === null ? W.memoizedState = ee = e : ee = ee.next = e;
  }
  return ee;
}
function Zn(e, t) {
  return typeof t == "function" ? t(e) : t;
}
function Wl(e) {
  var t = ze(), n = t.queue;
  if (n === null) throw Error(S(311));
  n.lastRenderedReducer = e;
  var r = J, l = r.baseQueue, i = n.pending;
  if (i !== null) {
    if (l !== null) {
      var o = l.next;
      l.next = i.next, i.next = o;
    }
    r.baseQueue = l = i, n.pending = null;
  }
  if (l !== null) {
    i = l.next, r = r.baseState;
    var u = o = null, s = null, c = i;
    do {
      var h = c.lane;
      if ((zt & h) === h) s !== null && (s = s.next = { lane: 0, action: c.action, hasEagerState: c.hasEagerState, eagerState: c.eagerState, next: null }), r = c.hasEagerState ? c.eagerState : e(r, c.action);
      else {
        var v = {
          lane: h,
          action: c.action,
          hasEagerState: c.hasEagerState,
          eagerState: c.eagerState,
          next: null
        };
        s === null ? (u = s = v, o = r) : s = s.next = v, W.lanes |= h, Rt |= h;
      }
      c = c.next;
    } while (c !== null && c !== i);
    s === null ? o = r : s.next = u, Ue(r, t.memoizedState) || (he = !0), t.memoizedState = r, t.baseState = o, t.baseQueue = s, n.lastRenderedState = r;
  }
  if (e = n.interleaved, e !== null) {
    l = e;
    do
      i = l.lane, W.lanes |= i, Rt |= i, l = l.next;
    while (l !== e);
  } else l === null && (n.lanes = 0);
  return [t.memoizedState, n.dispatch];
}
function Kl(e) {
  var t = ze(), n = t.queue;
  if (n === null) throw Error(S(311));
  n.lastRenderedReducer = e;
  var r = n.dispatch, l = n.pending, i = t.memoizedState;
  if (l !== null) {
    n.pending = null;
    var o = l = l.next;
    do
      i = e(i, o.action), o = o.next;
    while (o !== l);
    Ue(i, t.memoizedState) || (he = !0), t.memoizedState = i, t.baseQueue === null && (t.baseState = i), n.lastRenderedState = i;
  }
  return [i, r];
}
function Na() {
}
function Pa(e, t) {
  var n = W, r = ze(), l = t(), i = !Ue(r.memoizedState, l);
  if (i && (r.memoizedState = l, he = !0), r = r.queue, Po(Ta.bind(null, n, r, e), [e]), r.getSnapshot !== t || i || ee !== null && ee.memoizedState.tag & 1) {
    if (n.flags |= 2048, Jn(9, La.bind(null, n, r, l, t), void 0, null), te === null) throw Error(S(349));
    zt & 30 || ja(n, t, l);
  }
  return l;
}
function ja(e, t, n) {
  e.flags |= 16384, e = { getSnapshot: t, value: n }, t = W.updateQueue, t === null ? (t = { lastEffect: null, stores: null }, W.updateQueue = t, t.stores = [e]) : (n = t.stores, n === null ? t.stores = [e] : n.push(e));
}
function La(e, t, n, r) {
  t.value = n, t.getSnapshot = r, za(t) && Ra(e);
}
function Ta(e, t, n) {
  return n(function() {
    za(t) && Ra(e);
  });
}
function za(e) {
  var t = e.getSnapshot;
  e = e.value;
  try {
    var n = t();
    return !Ue(e, n);
  } catch {
    return !0;
  }
}
function Ra(e) {
  var t = Je(e, 1);
  t !== null && Fe(t, e, 1, -1);
}
function Lu(e) {
  var t = Ve();
  return typeof e == "function" && (e = e()), t.memoizedState = t.baseState = e, e = { pending: null, interleaved: null, lanes: 0, dispatch: null, lastRenderedReducer: Zn, lastRenderedState: e }, t.queue = e, e = e.dispatch = _d.bind(null, W, e), [t.memoizedState, e];
}
function Jn(e, t, n, r) {
  return e = { tag: e, create: t, destroy: n, deps: r, next: null }, t = W.updateQueue, t === null ? (t = { lastEffect: null, stores: null }, W.updateQueue = t, t.lastEffect = e.next = e) : (n = t.lastEffect, n === null ? t.lastEffect = e.next = e : (r = n.next, n.next = e, e.next = r, t.lastEffect = e)), e;
}
function Ma() {
  return ze().memoizedState;
}
function zr(e, t, n, r) {
  var l = Ve();
  W.flags |= e, l.memoizedState = Jn(1 | t, n, void 0, r === void 0 ? null : r);
}
function ml(e, t, n, r) {
  var l = ze();
  r = r === void 0 ? null : r;
  var i = void 0;
  if (J !== null) {
    var o = J.memoizedState;
    if (i = o.destroy, r !== null && Co(r, o.deps)) {
      l.memoizedState = Jn(t, n, i, r);
      return;
    }
  }
  W.flags |= e, l.memoizedState = Jn(1 | t, n, i, r);
}
function Tu(e, t) {
  return zr(8390656, 8, e, t);
}
function Po(e, t) {
  return ml(2048, 8, e, t);
}
function Ia(e, t) {
  return ml(4, 2, e, t);
}
function Da(e, t) {
  return ml(4, 4, e, t);
}
function Oa(e, t) {
  if (typeof t == "function") return e = e(), t(e), function() {
    t(null);
  };
  if (t != null) return e = e(), t.current = e, function() {
    t.current = null;
  };
}
function Fa(e, t, n) {
  return n = n != null ? n.concat([e]) : null, ml(4, 4, Oa.bind(null, t, e), n);
}
function jo() {
}
function Ua(e, t) {
  var n = ze();
  t = t === void 0 ? null : t;
  var r = n.memoizedState;
  return r !== null && t !== null && Co(t, r[1]) ? r[0] : (n.memoizedState = [e, t], e);
}
function Aa(e, t) {
  var n = ze();
  t = t === void 0 ? null : t;
  var r = n.memoizedState;
  return r !== null && t !== null && Co(t, r[1]) ? r[0] : (e = e(), n.memoizedState = [e, t], e);
}
function Va(e, t, n) {
  return zt & 21 ? (Ue(n, t) || (n = Ws(), W.lanes |= n, Rt |= n, e.baseState = !0), t) : (e.baseState && (e.baseState = !1, he = !0), e.memoizedState = n);
}
function Ed(e, t) {
  var n = F;
  F = n !== 0 && 4 > n ? n : 4, e(!0);
  var r = Ql.transition;
  Ql.transition = {};
  try {
    e(!1), t();
  } finally {
    F = n, Ql.transition = r;
  }
}
function $a() {
  return ze().memoizedState;
}
function Cd(e, t, n) {
  var r = pt(e);
  if (n = { lane: r, action: n, hasEagerState: !1, eagerState: null, next: null }, Ba(e)) Ha(t, n);
  else if (n = Ea(e, t, n, r), n !== null) {
    var l = fe();
    Fe(n, e, r, l), Qa(n, t, r);
  }
}
function _d(e, t, n) {
  var r = pt(e), l = { lane: r, action: n, hasEagerState: !1, eagerState: null, next: null };
  if (Ba(e)) Ha(t, l);
  else {
    var i = e.alternate;
    if (e.lanes === 0 && (i === null || i.lanes === 0) && (i = t.lastRenderedReducer, i !== null)) try {
      var o = t.lastRenderedState, u = i(o, n);
      if (l.hasEagerState = !0, l.eagerState = u, Ue(u, o)) {
        var s = t.interleaved;
        s === null ? (l.next = l, wo(t)) : (l.next = s.next, s.next = l), t.interleaved = l;
        return;
      }
    } catch {
    } finally {
    }
    n = Ea(e, t, l, r), n !== null && (l = fe(), Fe(n, e, r, l), Qa(n, t, r));
  }
}
function Ba(e) {
  var t = e.alternate;
  return e === W || t !== null && t === W;
}
function Ha(e, t) {
  Rn = el = !0;
  var n = e.pending;
  n === null ? t.next = t : (t.next = n.next, n.next = t), e.pending = t;
}
function Qa(e, t, n) {
  if (n & 4194240) {
    var r = t.lanes;
    r &= e.pendingLanes, n |= r, t.lanes = n, lo(e, n);
  }
}
var tl = { readContext: Te, useCallback: ie, useContext: ie, useEffect: ie, useImperativeHandle: ie, useInsertionEffect: ie, useLayoutEffect: ie, useMemo: ie, useReducer: ie, useRef: ie, useState: ie, useDebugValue: ie, useDeferredValue: ie, useTransition: ie, useMutableSource: ie, useSyncExternalStore: ie, useId: ie, unstable_isNewReconciler: !1 }, Nd = { readContext: Te, useCallback: function(e, t) {
  return Ve().memoizedState = [e, t === void 0 ? null : t], e;
}, useContext: Te, useEffect: Tu, useImperativeHandle: function(e, t, n) {
  return n = n != null ? n.concat([e]) : null, zr(
    4194308,
    4,
    Oa.bind(null, t, e),
    n
  );
}, useLayoutEffect: function(e, t) {
  return zr(4194308, 4, e, t);
}, useInsertionEffect: function(e, t) {
  return zr(4, 2, e, t);
}, useMemo: function(e, t) {
  var n = Ve();
  return t = t === void 0 ? null : t, e = e(), n.memoizedState = [e, t], e;
}, useReducer: function(e, t, n) {
  var r = Ve();
  return t = n !== void 0 ? n(t) : t, r.memoizedState = r.baseState = t, e = { pending: null, interleaved: null, lanes: 0, dispatch: null, lastRenderedReducer: e, lastRenderedState: t }, r.queue = e, e = e.dispatch = Cd.bind(null, W, e), [r.memoizedState, e];
}, useRef: function(e) {
  var t = Ve();
  return e = { current: e }, t.memoizedState = e;
}, useState: Lu, useDebugValue: jo, useDeferredValue: function(e) {
  return Ve().memoizedState = e;
}, useTransition: function() {
  var e = Lu(!1), t = e[0];
  return e = Ed.bind(null, e[1]), Ve().memoizedState = e, [t, e];
}, useMutableSource: function() {
}, useSyncExternalStore: function(e, t, n) {
  var r = W, l = Ve();
  if (H) {
    if (n === void 0) throw Error(S(407));
    n = n();
  } else {
    if (n = t(), te === null) throw Error(S(349));
    zt & 30 || ja(r, t, n);
  }
  l.memoizedState = n;
  var i = { value: n, getSnapshot: t };
  return l.queue = i, Tu(Ta.bind(
    null,
    r,
    i,
    e
  ), [e]), r.flags |= 2048, Jn(9, La.bind(null, r, i, n, t), void 0, null), n;
}, useId: function() {
  var e = Ve(), t = te.identifierPrefix;
  if (H) {
    var n = Ye, r = Ke;
    n = (r & ~(1 << 32 - Oe(r) - 1)).toString(32) + n, t = ":" + t + "R" + n, n = Gn++, 0 < n && (t += "H" + n.toString(32)), t += ":";
  } else n = xd++, t = ":" + t + "r" + n.toString(32) + ":";
  return e.memoizedState = t;
}, unstable_isNewReconciler: !1 }, Pd = {
  readContext: Te,
  useCallback: Ua,
  useContext: Te,
  useEffect: Po,
  useImperativeHandle: Fa,
  useInsertionEffect: Ia,
  useLayoutEffect: Da,
  useMemo: Aa,
  useReducer: Wl,
  useRef: Ma,
  useState: function() {
    return Wl(Zn);
  },
  useDebugValue: jo,
  useDeferredValue: function(e) {
    var t = ze();
    return Va(t, J.memoizedState, e);
  },
  useTransition: function() {
    var e = Wl(Zn)[0], t = ze().memoizedState;
    return [e, t];
  },
  useMutableSource: Na,
  useSyncExternalStore: Pa,
  useId: $a,
  unstable_isNewReconciler: !1
}, jd = { readContext: Te, useCallback: Ua, useContext: Te, useEffect: Po, useImperativeHandle: Fa, useInsertionEffect: Ia, useLayoutEffect: Da, useMemo: Aa, useReducer: Kl, useRef: Ma, useState: function() {
  return Kl(Zn);
}, useDebugValue: jo, useDeferredValue: function(e) {
  var t = ze();
  return J === null ? t.memoizedState = e : Va(t, J.memoizedState, e);
}, useTransition: function() {
  var e = Kl(Zn)[0], t = ze().memoizedState;
  return [e, t];
}, useMutableSource: Na, useSyncExternalStore: Pa, useId: $a, unstable_isNewReconciler: !1 };
function Me(e, t) {
  if (e && e.defaultProps) {
    t = K({}, t), e = e.defaultProps;
    for (var n in e) t[n] === void 0 && (t[n] = e[n]);
    return t;
  }
  return t;
}
function Ti(e, t, n, r) {
  t = e.memoizedState, n = n(r, t), n = n == null ? t : K({}, t, n), e.memoizedState = n, e.lanes === 0 && (e.updateQueue.baseState = n);
}
var hl = { isMounted: function(e) {
  return (e = e._reactInternals) ? Dt(e) === e : !1;
}, enqueueSetState: function(e, t, n) {
  e = e._reactInternals;
  var r = fe(), l = pt(e), i = Xe(r, l);
  i.payload = t, n != null && (i.callback = n), t = ft(e, i, l), t !== null && (Fe(t, e, l, r), Lr(t, e, l));
}, enqueueReplaceState: function(e, t, n) {
  e = e._reactInternals;
  var r = fe(), l = pt(e), i = Xe(r, l);
  i.tag = 1, i.payload = t, n != null && (i.callback = n), t = ft(e, i, l), t !== null && (Fe(t, e, l, r), Lr(t, e, l));
}, enqueueForceUpdate: function(e, t) {
  e = e._reactInternals;
  var n = fe(), r = pt(e), l = Xe(n, r);
  l.tag = 2, t != null && (l.callback = t), t = ft(e, l, r), t !== null && (Fe(t, e, r, n), Lr(t, e, r));
} };
function zu(e, t, n, r, l, i, o) {
  return e = e.stateNode, typeof e.shouldComponentUpdate == "function" ? e.shouldComponentUpdate(r, i, o) : t.prototype && t.prototype.isPureReactComponent ? !Hn(n, r) || !Hn(l, i) : !0;
}
function Wa(e, t, n) {
  var r = !1, l = vt, i = t.contextType;
  return typeof i == "object" && i !== null ? i = Te(i) : (l = ge(t) ? Lt : se.current, r = t.contextTypes, i = (r = r != null) ? nn(e, l) : vt), t = new t(n, i), e.memoizedState = t.state !== null && t.state !== void 0 ? t.state : null, t.updater = hl, e.stateNode = t, t._reactInternals = e, r && (e = e.stateNode, e.__reactInternalMemoizedUnmaskedChildContext = l, e.__reactInternalMemoizedMaskedChildContext = i), t;
}
function Ru(e, t, n, r) {
  e = t.state, typeof t.componentWillReceiveProps == "function" && t.componentWillReceiveProps(n, r), typeof t.UNSAFE_componentWillReceiveProps == "function" && t.UNSAFE_componentWillReceiveProps(n, r), t.state !== e && hl.enqueueReplaceState(t, t.state, null);
}
function zi(e, t, n, r) {
  var l = e.stateNode;
  l.props = n, l.state = e.memoizedState, l.refs = {}, ko(e);
  var i = t.contextType;
  typeof i == "object" && i !== null ? l.context = Te(i) : (i = ge(t) ? Lt : se.current, l.context = nn(e, i)), l.state = e.memoizedState, i = t.getDerivedStateFromProps, typeof i == "function" && (Ti(e, t, i, n), l.state = e.memoizedState), typeof t.getDerivedStateFromProps == "function" || typeof l.getSnapshotBeforeUpdate == "function" || typeof l.UNSAFE_componentWillMount != "function" && typeof l.componentWillMount != "function" || (t = l.state, typeof l.componentWillMount == "function" && l.componentWillMount(), typeof l.UNSAFE_componentWillMount == "function" && l.UNSAFE_componentWillMount(), t !== l.state && hl.enqueueReplaceState(l, l.state, null), qr(e, n, l, r), l.state = e.memoizedState), typeof l.componentDidMount == "function" && (e.flags |= 4194308);
}
function un(e, t) {
  try {
    var n = "", r = t;
    do
      n += tf(r), r = r.return;
    while (r);
    var l = n;
  } catch (i) {
    l = `
Error generating stack: ` + i.message + `
` + i.stack;
  }
  return { value: e, source: t, stack: l, digest: null };
}
function Yl(e, t, n) {
  return { value: e, source: null, stack: n ?? null, digest: t ?? null };
}
function Ri(e, t) {
  try {
    console.error(t.value);
  } catch (n) {
    setTimeout(function() {
      throw n;
    });
  }
}
var Ld = typeof WeakMap == "function" ? WeakMap : Map;
function Ka(e, t, n) {
  n = Xe(-1, n), n.tag = 3, n.payload = { element: null };
  var r = t.value;
  return n.callback = function() {
    rl || (rl = !0, Bi = r), Ri(e, t);
  }, n;
}
function Ya(e, t, n) {
  n = Xe(-1, n), n.tag = 3;
  var r = e.type.getDerivedStateFromError;
  if (typeof r == "function") {
    var l = t.value;
    n.payload = function() {
      return r(l);
    }, n.callback = function() {
      Ri(e, t);
    };
  }
  var i = e.stateNode;
  return i !== null && typeof i.componentDidCatch == "function" && (n.callback = function() {
    Ri(e, t), typeof r != "function" && (dt === null ? dt = /* @__PURE__ */ new Set([this]) : dt.add(this));
    var o = t.stack;
    this.componentDidCatch(t.value, { componentStack: o !== null ? o : "" });
  }), n;
}
function Mu(e, t, n) {
  var r = e.pingCache;
  if (r === null) {
    r = e.pingCache = new Ld();
    var l = /* @__PURE__ */ new Set();
    r.set(t, l);
  } else l = r.get(t), l === void 0 && (l = /* @__PURE__ */ new Set(), r.set(t, l));
  l.has(n) || (l.add(n), e = Hd.bind(null, e, t, n), t.then(e, e));
}
function Iu(e) {
  do {
    var t;
    if ((t = e.tag === 13) && (t = e.memoizedState, t = t !== null ? t.dehydrated !== null : !0), t) return e;
    e = e.return;
  } while (e !== null);
  return null;
}
function Du(e, t, n, r, l) {
  return e.mode & 1 ? (e.flags |= 65536, e.lanes = l, e) : (e === t ? e.flags |= 65536 : (e.flags |= 128, n.flags |= 131072, n.flags &= -52805, n.tag === 1 && (n.alternate === null ? n.tag = 17 : (t = Xe(-1, 1), t.tag = 2, ft(n, t, 1))), n.lanes |= 1), e);
}
var Td = be.ReactCurrentOwner, he = !1;
function ce(e, t, n, r) {
  t.child = e === null ? xa(t, null, n, r) : ln(t, e.child, n, r);
}
function Ou(e, t, n, r, l) {
  n = n.render;
  var i = t.ref;
  return bt(t, l), r = _o(e, t, n, r, i, l), n = No(), e !== null && !he ? (t.updateQueue = e.updateQueue, t.flags &= -2053, e.lanes &= ~l, qe(e, t, l)) : (H && n && po(t), t.flags |= 1, ce(e, t, r, l), t.child);
}
function Fu(e, t, n, r, l) {
  if (e === null) {
    var i = n.type;
    return typeof i == "function" && !Oo(i) && i.defaultProps === void 0 && n.compare === null && n.defaultProps === void 0 ? (t.tag = 15, t.type = i, Xa(e, t, i, r, l)) : (e = Dr(n.type, null, r, t, t.mode, l), e.ref = t.ref, e.return = t, t.child = e);
  }
  if (i = e.child, !(e.lanes & l)) {
    var o = i.memoizedProps;
    if (n = n.compare, n = n !== null ? n : Hn, n(o, r) && e.ref === t.ref) return qe(e, t, l);
  }
  return t.flags |= 1, e = mt(i, r), e.ref = t.ref, e.return = t, t.child = e;
}
function Xa(e, t, n, r, l) {
  if (e !== null) {
    var i = e.memoizedProps;
    if (Hn(i, r) && e.ref === t.ref) if (he = !1, t.pendingProps = r = i, (e.lanes & l) !== 0) e.flags & 131072 && (he = !0);
    else return t.lanes = e.lanes, qe(e, t, l);
  }
  return Mi(e, t, n, r, l);
}
function Ga(e, t, n) {
  var r = t.pendingProps, l = r.children, i = e !== null ? e.memoizedState : null;
  if (r.mode === "hidden") if (!(t.mode & 1)) t.memoizedState = { baseLanes: 0, cachePool: null, transitions: null }, U(Xt, we), we |= n;
  else {
    if (!(n & 1073741824)) return e = i !== null ? i.baseLanes | n : n, t.lanes = t.childLanes = 1073741824, t.memoizedState = { baseLanes: e, cachePool: null, transitions: null }, t.updateQueue = null, U(Xt, we), we |= e, null;
    t.memoizedState = { baseLanes: 0, cachePool: null, transitions: null }, r = i !== null ? i.baseLanes : n, U(Xt, we), we |= r;
  }
  else i !== null ? (r = i.baseLanes | n, t.memoizedState = null) : r = n, U(Xt, we), we |= r;
  return ce(e, t, l, n), t.child;
}
function Za(e, t) {
  var n = t.ref;
  (e === null && n !== null || e !== null && e.ref !== n) && (t.flags |= 512, t.flags |= 2097152);
}
function Mi(e, t, n, r, l) {
  var i = ge(n) ? Lt : se.current;
  return i = nn(t, i), bt(t, l), n = _o(e, t, n, r, i, l), r = No(), e !== null && !he ? (t.updateQueue = e.updateQueue, t.flags &= -2053, e.lanes &= ~l, qe(e, t, l)) : (H && r && po(t), t.flags |= 1, ce(e, t, n, l), t.child);
}
function Uu(e, t, n, r, l) {
  if (ge(n)) {
    var i = !0;
    Yr(t);
  } else i = !1;
  if (bt(t, l), t.stateNode === null) Rr(e, t), Wa(t, n, r), zi(t, n, r, l), r = !0;
  else if (e === null) {
    var o = t.stateNode, u = t.memoizedProps;
    o.props = u;
    var s = o.context, c = n.contextType;
    typeof c == "object" && c !== null ? c = Te(c) : (c = ge(n) ? Lt : se.current, c = nn(t, c));
    var h = n.getDerivedStateFromProps, v = typeof h == "function" || typeof o.getSnapshotBeforeUpdate == "function";
    v || typeof o.UNSAFE_componentWillReceiveProps != "function" && typeof o.componentWillReceiveProps != "function" || (u !== r || s !== c) && Ru(t, o, r, c), nt = !1;
    var f = t.memoizedState;
    o.state = f, qr(t, r, o, l), s = t.memoizedState, u !== r || f !== s || ve.current || nt ? (typeof h == "function" && (Ti(t, n, h, r), s = t.memoizedState), (u = nt || zu(t, n, u, r, f, s, c)) ? (v || typeof o.UNSAFE_componentWillMount != "function" && typeof o.componentWillMount != "function" || (typeof o.componentWillMount == "function" && o.componentWillMount(), typeof o.UNSAFE_componentWillMount == "function" && o.UNSAFE_componentWillMount()), typeof o.componentDidMount == "function" && (t.flags |= 4194308)) : (typeof o.componentDidMount == "function" && (t.flags |= 4194308), t.memoizedProps = r, t.memoizedState = s), o.props = r, o.state = s, o.context = c, r = u) : (typeof o.componentDidMount == "function" && (t.flags |= 4194308), r = !1);
  } else {
    o = t.stateNode, Ca(e, t), u = t.memoizedProps, c = t.type === t.elementType ? u : Me(t.type, u), o.props = c, v = t.pendingProps, f = o.context, s = n.contextType, typeof s == "object" && s !== null ? s = Te(s) : (s = ge(n) ? Lt : se.current, s = nn(t, s));
    var w = n.getDerivedStateFromProps;
    (h = typeof w == "function" || typeof o.getSnapshotBeforeUpdate == "function") || typeof o.UNSAFE_componentWillReceiveProps != "function" && typeof o.componentWillReceiveProps != "function" || (u !== v || f !== s) && Ru(t, o, r, s), nt = !1, f = t.memoizedState, o.state = f, qr(t, r, o, l);
    var k = t.memoizedState;
    u !== v || f !== k || ve.current || nt ? (typeof w == "function" && (Ti(t, n, w, r), k = t.memoizedState), (c = nt || zu(t, n, c, r, f, k, s) || !1) ? (h || typeof o.UNSAFE_componentWillUpdate != "function" && typeof o.componentWillUpdate != "function" || (typeof o.componentWillUpdate == "function" && o.componentWillUpdate(r, k, s), typeof o.UNSAFE_componentWillUpdate == "function" && o.UNSAFE_componentWillUpdate(r, k, s)), typeof o.componentDidUpdate == "function" && (t.flags |= 4), typeof o.getSnapshotBeforeUpdate == "function" && (t.flags |= 1024)) : (typeof o.componentDidUpdate != "function" || u === e.memoizedProps && f === e.memoizedState || (t.flags |= 4), typeof o.getSnapshotBeforeUpdate != "function" || u === e.memoizedProps && f === e.memoizedState || (t.flags |= 1024), t.memoizedProps = r, t.memoizedState = k), o.props = r, o.state = k, o.context = s, r = c) : (typeof o.componentDidUpdate != "function" || u === e.memoizedProps && f === e.memoizedState || (t.flags |= 4), typeof o.getSnapshotBeforeUpdate != "function" || u === e.memoizedProps && f === e.memoizedState || (t.flags |= 1024), r = !1);
  }
  return Ii(e, t, n, r, i, l);
}
function Ii(e, t, n, r, l, i) {
  Za(e, t);
  var o = (t.flags & 128) !== 0;
  if (!r && !o) return l && Eu(t, n, !1), qe(e, t, i);
  r = t.stateNode, Td.current = t;
  var u = o && typeof n.getDerivedStateFromError != "function" ? null : r.render();
  return t.flags |= 1, e !== null && o ? (t.child = ln(t, e.child, null, i), t.child = ln(t, null, u, i)) : ce(e, t, u, i), t.memoizedState = r.state, l && Eu(t, n, !0), t.child;
}
function Ja(e) {
  var t = e.stateNode;
  t.pendingContext ? xu(e, t.pendingContext, t.pendingContext !== t.context) : t.context && xu(e, t.context, !1), So(e, t.containerInfo);
}
function Au(e, t, n, r, l) {
  return rn(), ho(l), t.flags |= 256, ce(e, t, n, r), t.child;
}
var Di = { dehydrated: null, treeContext: null, retryLane: 0 };
function Oi(e) {
  return { baseLanes: e, cachePool: null, transitions: null };
}
function qa(e, t, n) {
  var r = t.pendingProps, l = Q.current, i = !1, o = (t.flags & 128) !== 0, u;
  if ((u = o) || (u = e !== null && e.memoizedState === null ? !1 : (l & 2) !== 0), u ? (i = !0, t.flags &= -129) : (e === null || e.memoizedState !== null) && (l |= 1), U(Q, l & 1), e === null)
    return ji(t), e = t.memoizedState, e !== null && (e = e.dehydrated, e !== null) ? (t.mode & 1 ? e.data === "$!" ? t.lanes = 8 : t.lanes = 1073741824 : t.lanes = 1, null) : (o = r.children, e = r.fallback, i ? (r = t.mode, i = t.child, o = { mode: "hidden", children: o }, !(r & 1) && i !== null ? (i.childLanes = 0, i.pendingProps = o) : i = yl(o, r, 0, null), e = jt(e, r, n, null), i.return = t, e.return = t, i.sibling = e, t.child = i, t.child.memoizedState = Oi(n), t.memoizedState = Di, e) : Lo(t, o));
  if (l = e.memoizedState, l !== null && (u = l.dehydrated, u !== null)) return zd(e, t, o, r, u, l, n);
  if (i) {
    i = r.fallback, o = t.mode, l = e.child, u = l.sibling;
    var s = { mode: "hidden", children: r.children };
    return !(o & 1) && t.child !== l ? (r = t.child, r.childLanes = 0, r.pendingProps = s, t.deletions = null) : (r = mt(l, s), r.subtreeFlags = l.subtreeFlags & 14680064), u !== null ? i = mt(u, i) : (i = jt(i, o, n, null), i.flags |= 2), i.return = t, r.return = t, r.sibling = i, t.child = r, r = i, i = t.child, o = e.child.memoizedState, o = o === null ? Oi(n) : { baseLanes: o.baseLanes | n, cachePool: null, transitions: o.transitions }, i.memoizedState = o, i.childLanes = e.childLanes & ~n, t.memoizedState = Di, r;
  }
  return i = e.child, e = i.sibling, r = mt(i, { mode: "visible", children: r.children }), !(t.mode & 1) && (r.lanes = n), r.return = t, r.sibling = null, e !== null && (n = t.deletions, n === null ? (t.deletions = [e], t.flags |= 16) : n.push(e)), t.child = r, t.memoizedState = null, r;
}
function Lo(e, t) {
  return t = yl({ mode: "visible", children: t }, e.mode, 0, null), t.return = e, e.child = t;
}
function kr(e, t, n, r) {
  return r !== null && ho(r), ln(t, e.child, null, n), e = Lo(t, t.pendingProps.children), e.flags |= 2, t.memoizedState = null, e;
}
function zd(e, t, n, r, l, i, o) {
  if (n)
    return t.flags & 256 ? (t.flags &= -257, r = Yl(Error(S(422))), kr(e, t, o, r)) : t.memoizedState !== null ? (t.child = e.child, t.flags |= 128, null) : (i = r.fallback, l = t.mode, r = yl({ mode: "visible", children: r.children }, l, 0, null), i = jt(i, l, o, null), i.flags |= 2, r.return = t, i.return = t, r.sibling = i, t.child = r, t.mode & 1 && ln(t, e.child, null, o), t.child.memoizedState = Oi(o), t.memoizedState = Di, i);
  if (!(t.mode & 1)) return kr(e, t, o, null);
  if (l.data === "$!") {
    if (r = l.nextSibling && l.nextSibling.dataset, r) var u = r.dgst;
    return r = u, i = Error(S(419)), r = Yl(i, r, void 0), kr(e, t, o, r);
  }
  if (u = (o & e.childLanes) !== 0, he || u) {
    if (r = te, r !== null) {
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
      l = l & (r.suspendedLanes | o) ? 0 : l, l !== 0 && l !== i.retryLane && (i.retryLane = l, Je(e, l), Fe(r, e, l, -1));
    }
    return Do(), r = Yl(Error(S(421))), kr(e, t, o, r);
  }
  return l.data === "$?" ? (t.flags |= 128, t.child = e.child, t = Qd.bind(null, e), l._reactRetry = t, null) : (e = i.treeContext, ke = ct(l.nextSibling), Se = t, H = !0, De = null, e !== null && (Ne[Pe++] = Ke, Ne[Pe++] = Ye, Ne[Pe++] = Tt, Ke = e.id, Ye = e.overflow, Tt = t), t = Lo(t, r.children), t.flags |= 4096, t);
}
function Vu(e, t, n) {
  e.lanes |= t;
  var r = e.alternate;
  r !== null && (r.lanes |= t), Li(e.return, t, n);
}
function Xl(e, t, n, r, l) {
  var i = e.memoizedState;
  i === null ? e.memoizedState = { isBackwards: t, rendering: null, renderingStartTime: 0, last: r, tail: n, tailMode: l } : (i.isBackwards = t, i.rendering = null, i.renderingStartTime = 0, i.last = r, i.tail = n, i.tailMode = l);
}
function ba(e, t, n) {
  var r = t.pendingProps, l = r.revealOrder, i = r.tail;
  if (ce(e, t, r.children, n), r = Q.current, r & 2) r = r & 1 | 2, t.flags |= 128;
  else {
    if (e !== null && e.flags & 128) e: for (e = t.child; e !== null; ) {
      if (e.tag === 13) e.memoizedState !== null && Vu(e, n, t);
      else if (e.tag === 19) Vu(e, n, t);
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
  if (U(Q, r), !(t.mode & 1)) t.memoizedState = null;
  else switch (l) {
    case "forwards":
      for (n = t.child, l = null; n !== null; ) e = n.alternate, e !== null && br(e) === null && (l = n), n = n.sibling;
      n = l, n === null ? (l = t.child, t.child = null) : (l = n.sibling, n.sibling = null), Xl(t, !1, l, n, i);
      break;
    case "backwards":
      for (n = null, l = t.child, t.child = null; l !== null; ) {
        if (e = l.alternate, e !== null && br(e) === null) {
          t.child = l;
          break;
        }
        e = l.sibling, l.sibling = n, n = l, l = e;
      }
      Xl(t, !0, n, null, i);
      break;
    case "together":
      Xl(t, !1, null, null, void 0);
      break;
    default:
      t.memoizedState = null;
  }
  return t.child;
}
function Rr(e, t) {
  !(t.mode & 1) && e !== null && (e.alternate = null, t.alternate = null, t.flags |= 2);
}
function qe(e, t, n) {
  if (e !== null && (t.dependencies = e.dependencies), Rt |= t.lanes, !(n & t.childLanes)) return null;
  if (e !== null && t.child !== e.child) throw Error(S(153));
  if (t.child !== null) {
    for (e = t.child, n = mt(e, e.pendingProps), t.child = n, n.return = t; e.sibling !== null; ) e = e.sibling, n = n.sibling = mt(e, e.pendingProps), n.return = t;
    n.sibling = null;
  }
  return t.child;
}
function Rd(e, t, n) {
  switch (t.tag) {
    case 3:
      Ja(t), rn();
      break;
    case 5:
      _a(t);
      break;
    case 1:
      ge(t.type) && Yr(t);
      break;
    case 4:
      So(t, t.stateNode.containerInfo);
      break;
    case 10:
      var r = t.type._context, l = t.memoizedProps.value;
      U(Zr, r._currentValue), r._currentValue = l;
      break;
    case 13:
      if (r = t.memoizedState, r !== null)
        return r.dehydrated !== null ? (U(Q, Q.current & 1), t.flags |= 128, null) : n & t.child.childLanes ? qa(e, t, n) : (U(Q, Q.current & 1), e = qe(e, t, n), e !== null ? e.sibling : null);
      U(Q, Q.current & 1);
      break;
    case 19:
      if (r = (n & t.childLanes) !== 0, e.flags & 128) {
        if (r) return ba(e, t, n);
        t.flags |= 128;
      }
      if (l = t.memoizedState, l !== null && (l.rendering = null, l.tail = null, l.lastEffect = null), U(Q, Q.current), r) break;
      return null;
    case 22:
    case 23:
      return t.lanes = 0, Ga(e, t, n);
  }
  return qe(e, t, n);
}
var ec, Fi, tc, nc;
ec = function(e, t) {
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
Fi = function() {
};
tc = function(e, t, n, r) {
  var l = e.memoizedProps;
  if (l !== r) {
    e = t.stateNode, Nt(He.current);
    var i = null;
    switch (n) {
      case "input":
        l = ii(e, l), r = ii(e, r), i = [];
        break;
      case "select":
        l = K({}, l, { value: void 0 }), r = K({}, r, { value: void 0 }), i = [];
        break;
      case "textarea":
        l = si(e, l), r = si(e, r), i = [];
        break;
      default:
        typeof l.onClick != "function" && typeof r.onClick == "function" && (e.onclick = Wr);
    }
    ci(n, r);
    var o;
    n = null;
    for (c in l) if (!r.hasOwnProperty(c) && l.hasOwnProperty(c) && l[c] != null) if (c === "style") {
      var u = l[c];
      for (o in u) u.hasOwnProperty(o) && (n || (n = {}), n[o] = "");
    } else c !== "dangerouslySetInnerHTML" && c !== "children" && c !== "suppressContentEditableWarning" && c !== "suppressHydrationWarning" && c !== "autoFocus" && (On.hasOwnProperty(c) ? i || (i = []) : (i = i || []).push(c, null));
    for (c in r) {
      var s = r[c];
      if (u = l != null ? l[c] : void 0, r.hasOwnProperty(c) && s !== u && (s != null || u != null)) if (c === "style") if (u) {
        for (o in u) !u.hasOwnProperty(o) || s && s.hasOwnProperty(o) || (n || (n = {}), n[o] = "");
        for (o in s) s.hasOwnProperty(o) && u[o] !== s[o] && (n || (n = {}), n[o] = s[o]);
      } else n || (i || (i = []), i.push(
        c,
        n
      )), n = s;
      else c === "dangerouslySetInnerHTML" ? (s = s ? s.__html : void 0, u = u ? u.__html : void 0, s != null && u !== s && (i = i || []).push(c, s)) : c === "children" ? typeof s != "string" && typeof s != "number" || (i = i || []).push(c, "" + s) : c !== "suppressContentEditableWarning" && c !== "suppressHydrationWarning" && (On.hasOwnProperty(c) ? (s != null && c === "onScroll" && V("scroll", e), i || u === s || (i = [])) : (i = i || []).push(c, s));
    }
    n && (i = i || []).push("style", n);
    var c = i;
    (t.updateQueue = c) && (t.flags |= 4);
  }
};
nc = function(e, t, n, r) {
  n !== r && (t.flags |= 4);
};
function Sn(e, t) {
  if (!H) switch (e.tailMode) {
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
function oe(e) {
  var t = e.alternate !== null && e.alternate.child === e.child, n = 0, r = 0;
  if (t) for (var l = e.child; l !== null; ) n |= l.lanes | l.childLanes, r |= l.subtreeFlags & 14680064, r |= l.flags & 14680064, l.return = e, l = l.sibling;
  else for (l = e.child; l !== null; ) n |= l.lanes | l.childLanes, r |= l.subtreeFlags, r |= l.flags, l.return = e, l = l.sibling;
  return e.subtreeFlags |= r, e.childLanes = n, t;
}
function Md(e, t, n) {
  var r = t.pendingProps;
  switch (mo(t), t.tag) {
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
      return oe(t), null;
    case 1:
      return ge(t.type) && Kr(), oe(t), null;
    case 3:
      return r = t.stateNode, on(), $(ve), $(se), Eo(), r.pendingContext && (r.context = r.pendingContext, r.pendingContext = null), (e === null || e.child === null) && (yr(t) ? t.flags |= 4 : e === null || e.memoizedState.isDehydrated && !(t.flags & 256) || (t.flags |= 1024, De !== null && (Wi(De), De = null))), Fi(e, t), oe(t), null;
    case 5:
      xo(t);
      var l = Nt(Xn.current);
      if (n = t.type, e !== null && t.stateNode != null) tc(e, t, n, r, l), e.ref !== t.ref && (t.flags |= 512, t.flags |= 2097152);
      else {
        if (!r) {
          if (t.stateNode === null) throw Error(S(166));
          return oe(t), null;
        }
        if (e = Nt(He.current), yr(t)) {
          r = t.stateNode, n = t.type;
          var i = t.memoizedProps;
          switch (r[$e] = t, r[Kn] = i, e = (t.mode & 1) !== 0, n) {
            case "dialog":
              V("cancel", r), V("close", r);
              break;
            case "iframe":
            case "object":
            case "embed":
              V("load", r);
              break;
            case "video":
            case "audio":
              for (l = 0; l < Nn.length; l++) V(Nn[l], r);
              break;
            case "source":
              V("error", r);
              break;
            case "img":
            case "image":
            case "link":
              V(
                "error",
                r
              ), V("load", r);
              break;
            case "details":
              V("toggle", r);
              break;
            case "input":
              Go(r, i), V("invalid", r);
              break;
            case "select":
              r._wrapperState = { wasMultiple: !!i.multiple }, V("invalid", r);
              break;
            case "textarea":
              Jo(r, i), V("invalid", r);
          }
          ci(n, i), l = null;
          for (var o in i) if (i.hasOwnProperty(o)) {
            var u = i[o];
            o === "children" ? typeof u == "string" ? r.textContent !== u && (i.suppressHydrationWarning !== !0 && gr(r.textContent, u, e), l = ["children", u]) : typeof u == "number" && r.textContent !== "" + u && (i.suppressHydrationWarning !== !0 && gr(
              r.textContent,
              u,
              e
            ), l = ["children", "" + u]) : On.hasOwnProperty(o) && u != null && o === "onScroll" && V("scroll", r);
          }
          switch (n) {
            case "input":
              ar(r), Zo(r, i, !0);
              break;
            case "textarea":
              ar(r), qo(r);
              break;
            case "select":
            case "option":
              break;
            default:
              typeof i.onClick == "function" && (r.onclick = Wr);
          }
          r = l, t.updateQueue = r, r !== null && (t.flags |= 4);
        } else {
          o = l.nodeType === 9 ? l : l.ownerDocument, e === "http://www.w3.org/1999/xhtml" && (e = Ts(n)), e === "http://www.w3.org/1999/xhtml" ? n === "script" ? (e = o.createElement("div"), e.innerHTML = "<script><\/script>", e = e.removeChild(e.firstChild)) : typeof r.is == "string" ? e = o.createElement(n, { is: r.is }) : (e = o.createElement(n), n === "select" && (o = e, r.multiple ? o.multiple = !0 : r.size && (o.size = r.size))) : e = o.createElementNS(e, n), e[$e] = t, e[Kn] = r, ec(e, t, !1, !1), t.stateNode = e;
          e: {
            switch (o = fi(n, r), n) {
              case "dialog":
                V("cancel", e), V("close", e), l = r;
                break;
              case "iframe":
              case "object":
              case "embed":
                V("load", e), l = r;
                break;
              case "video":
              case "audio":
                for (l = 0; l < Nn.length; l++) V(Nn[l], e);
                l = r;
                break;
              case "source":
                V("error", e), l = r;
                break;
              case "img":
              case "image":
              case "link":
                V(
                  "error",
                  e
                ), V("load", e), l = r;
                break;
              case "details":
                V("toggle", e), l = r;
                break;
              case "input":
                Go(e, r), l = ii(e, r), V("invalid", e);
                break;
              case "option":
                l = r;
                break;
              case "select":
                e._wrapperState = { wasMultiple: !!r.multiple }, l = K({}, r, { value: void 0 }), V("invalid", e);
                break;
              case "textarea":
                Jo(e, r), l = si(e, r), V("invalid", e);
                break;
              default:
                l = r;
            }
            ci(n, l), u = l;
            for (i in u) if (u.hasOwnProperty(i)) {
              var s = u[i];
              i === "style" ? Ms(e, s) : i === "dangerouslySetInnerHTML" ? (s = s ? s.__html : void 0, s != null && zs(e, s)) : i === "children" ? typeof s == "string" ? (n !== "textarea" || s !== "") && Fn(e, s) : typeof s == "number" && Fn(e, "" + s) : i !== "suppressContentEditableWarning" && i !== "suppressHydrationWarning" && i !== "autoFocus" && (On.hasOwnProperty(i) ? s != null && i === "onScroll" && V("scroll", e) : s != null && qi(e, i, s, o));
            }
            switch (n) {
              case "input":
                ar(e), Zo(e, r, !1);
                break;
              case "textarea":
                ar(e), qo(e);
                break;
              case "option":
                r.value != null && e.setAttribute("value", "" + ht(r.value));
                break;
              case "select":
                e.multiple = !!r.multiple, i = r.value, i != null ? Gt(e, !!r.multiple, i, !1) : r.defaultValue != null && Gt(
                  e,
                  !!r.multiple,
                  r.defaultValue,
                  !0
                );
                break;
              default:
                typeof l.onClick == "function" && (e.onclick = Wr);
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
      return oe(t), null;
    case 6:
      if (e && t.stateNode != null) nc(e, t, e.memoizedProps, r);
      else {
        if (typeof r != "string" && t.stateNode === null) throw Error(S(166));
        if (n = Nt(Xn.current), Nt(He.current), yr(t)) {
          if (r = t.stateNode, n = t.memoizedProps, r[$e] = t, (i = r.nodeValue !== n) && (e = Se, e !== null)) switch (e.tag) {
            case 3:
              gr(r.nodeValue, n, (e.mode & 1) !== 0);
              break;
            case 5:
              e.memoizedProps.suppressHydrationWarning !== !0 && gr(r.nodeValue, n, (e.mode & 1) !== 0);
          }
          i && (t.flags |= 4);
        } else r = (n.nodeType === 9 ? n : n.ownerDocument).createTextNode(r), r[$e] = t, t.stateNode = r;
      }
      return oe(t), null;
    case 13:
      if ($(Q), r = t.memoizedState, e === null || e.memoizedState !== null && e.memoizedState.dehydrated !== null) {
        if (H && ke !== null && t.mode & 1 && !(t.flags & 128)) ka(), rn(), t.flags |= 98560, i = !1;
        else if (i = yr(t), r !== null && r.dehydrated !== null) {
          if (e === null) {
            if (!i) throw Error(S(318));
            if (i = t.memoizedState, i = i !== null ? i.dehydrated : null, !i) throw Error(S(317));
            i[$e] = t;
          } else rn(), !(t.flags & 128) && (t.memoizedState = null), t.flags |= 4;
          oe(t), i = !1;
        } else De !== null && (Wi(De), De = null), i = !0;
        if (!i) return t.flags & 65536 ? t : null;
      }
      return t.flags & 128 ? (t.lanes = n, t) : (r = r !== null, r !== (e !== null && e.memoizedState !== null) && r && (t.child.flags |= 8192, t.mode & 1 && (e === null || Q.current & 1 ? q === 0 && (q = 3) : Do())), t.updateQueue !== null && (t.flags |= 4), oe(t), null);
    case 4:
      return on(), Fi(e, t), e === null && Qn(t.stateNode.containerInfo), oe(t), null;
    case 10:
      return yo(t.type._context), oe(t), null;
    case 17:
      return ge(t.type) && Kr(), oe(t), null;
    case 19:
      if ($(Q), i = t.memoizedState, i === null) return oe(t), null;
      if (r = (t.flags & 128) !== 0, o = i.rendering, o === null) if (r) Sn(i, !1);
      else {
        if (q !== 0 || e !== null && e.flags & 128) for (e = t.child; e !== null; ) {
          if (o = br(e), o !== null) {
            for (t.flags |= 128, Sn(i, !1), r = o.updateQueue, r !== null && (t.updateQueue = r, t.flags |= 4), t.subtreeFlags = 0, r = n, n = t.child; n !== null; ) i = n, e = r, i.flags &= 14680066, o = i.alternate, o === null ? (i.childLanes = 0, i.lanes = e, i.child = null, i.subtreeFlags = 0, i.memoizedProps = null, i.memoizedState = null, i.updateQueue = null, i.dependencies = null, i.stateNode = null) : (i.childLanes = o.childLanes, i.lanes = o.lanes, i.child = o.child, i.subtreeFlags = 0, i.deletions = null, i.memoizedProps = o.memoizedProps, i.memoizedState = o.memoizedState, i.updateQueue = o.updateQueue, i.type = o.type, e = o.dependencies, i.dependencies = e === null ? null : { lanes: e.lanes, firstContext: e.firstContext }), n = n.sibling;
            return U(Q, Q.current & 1 | 2), t.child;
          }
          e = e.sibling;
        }
        i.tail !== null && G() > sn && (t.flags |= 128, r = !0, Sn(i, !1), t.lanes = 4194304);
      }
      else {
        if (!r) if (e = br(o), e !== null) {
          if (t.flags |= 128, r = !0, n = e.updateQueue, n !== null && (t.updateQueue = n, t.flags |= 4), Sn(i, !0), i.tail === null && i.tailMode === "hidden" && !o.alternate && !H) return oe(t), null;
        } else 2 * G() - i.renderingStartTime > sn && n !== 1073741824 && (t.flags |= 128, r = !0, Sn(i, !1), t.lanes = 4194304);
        i.isBackwards ? (o.sibling = t.child, t.child = o) : (n = i.last, n !== null ? n.sibling = o : t.child = o, i.last = o);
      }
      return i.tail !== null ? (t = i.tail, i.rendering = t, i.tail = t.sibling, i.renderingStartTime = G(), t.sibling = null, n = Q.current, U(Q, r ? n & 1 | 2 : n & 1), t) : (oe(t), null);
    case 22:
    case 23:
      return Io(), r = t.memoizedState !== null, e !== null && e.memoizedState !== null !== r && (t.flags |= 8192), r && t.mode & 1 ? we & 1073741824 && (oe(t), t.subtreeFlags & 6 && (t.flags |= 8192)) : oe(t), null;
    case 24:
      return null;
    case 25:
      return null;
  }
  throw Error(S(156, t.tag));
}
function Id(e, t) {
  switch (mo(t), t.tag) {
    case 1:
      return ge(t.type) && Kr(), e = t.flags, e & 65536 ? (t.flags = e & -65537 | 128, t) : null;
    case 3:
      return on(), $(ve), $(se), Eo(), e = t.flags, e & 65536 && !(e & 128) ? (t.flags = e & -65537 | 128, t) : null;
    case 5:
      return xo(t), null;
    case 13:
      if ($(Q), e = t.memoizedState, e !== null && e.dehydrated !== null) {
        if (t.alternate === null) throw Error(S(340));
        rn();
      }
      return e = t.flags, e & 65536 ? (t.flags = e & -65537 | 128, t) : null;
    case 19:
      return $(Q), null;
    case 4:
      return on(), null;
    case 10:
      return yo(t.type._context), null;
    case 22:
    case 23:
      return Io(), null;
    case 24:
      return null;
    default:
      return null;
  }
}
var Sr = !1, ue = !1, Dd = typeof WeakSet == "function" ? WeakSet : Set, L = null;
function Yt(e, t) {
  var n = e.ref;
  if (n !== null) if (typeof n == "function") try {
    n(null);
  } catch (r) {
    Y(e, t, r);
  }
  else n.current = null;
}
function Ui(e, t, n) {
  try {
    n();
  } catch (r) {
    Y(e, t, r);
  }
}
var $u = !1;
function Od(e, t) {
  if (Si = Br, e = ua(), fo(e)) {
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
        var o = 0, u = -1, s = -1, c = 0, h = 0, v = e, f = null;
        t: for (; ; ) {
          for (var w; v !== n || l !== 0 && v.nodeType !== 3 || (u = o + l), v !== i || r !== 0 && v.nodeType !== 3 || (s = o + r), v.nodeType === 3 && (o += v.nodeValue.length), (w = v.firstChild) !== null; )
            f = v, v = w;
          for (; ; ) {
            if (v === e) break t;
            if (f === n && ++c === l && (u = o), f === i && ++h === r && (s = o), (w = v.nextSibling) !== null) break;
            v = f, f = v.parentNode;
          }
          v = w;
        }
        n = u === -1 || s === -1 ? null : { start: u, end: s };
      } else n = null;
    }
    n = n || { start: 0, end: 0 };
  } else n = null;
  for (xi = { focusedElem: e, selectionRange: n }, Br = !1, L = t; L !== null; ) if (t = L, e = t.child, (t.subtreeFlags & 1028) !== 0 && e !== null) e.return = t, L = e;
  else for (; L !== null; ) {
    t = L;
    try {
      var k = t.alternate;
      if (t.flags & 1024) switch (t.tag) {
        case 0:
        case 11:
        case 15:
          break;
        case 1:
          if (k !== null) {
            var E = k.memoizedProps, M = k.memoizedState, d = t.stateNode, a = d.getSnapshotBeforeUpdate(t.elementType === t.type ? E : Me(t.type, E), M);
            d.__reactInternalSnapshotBeforeUpdate = a;
          }
          break;
        case 3:
          var p = t.stateNode.containerInfo;
          p.nodeType === 1 ? p.textContent = "" : p.nodeType === 9 && p.documentElement && p.removeChild(p.documentElement);
          break;
        case 5:
        case 6:
        case 4:
        case 17:
          break;
        default:
          throw Error(S(163));
      }
    } catch (g) {
      Y(t, t.return, g);
    }
    if (e = t.sibling, e !== null) {
      e.return = t.return, L = e;
      break;
    }
    L = t.return;
  }
  return k = $u, $u = !1, k;
}
function Mn(e, t, n) {
  var r = t.updateQueue;
  if (r = r !== null ? r.lastEffect : null, r !== null) {
    var l = r = r.next;
    do {
      if ((l.tag & e) === e) {
        var i = l.destroy;
        l.destroy = void 0, i !== void 0 && Ui(t, n, i);
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
function Ai(e) {
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
function rc(e) {
  var t = e.alternate;
  t !== null && (e.alternate = null, rc(t)), e.child = null, e.deletions = null, e.sibling = null, e.tag === 5 && (t = e.stateNode, t !== null && (delete t[$e], delete t[Kn], delete t[_i], delete t[yd], delete t[wd])), e.stateNode = null, e.return = null, e.dependencies = null, e.memoizedProps = null, e.memoizedState = null, e.pendingProps = null, e.stateNode = null, e.updateQueue = null;
}
function lc(e) {
  return e.tag === 5 || e.tag === 3 || e.tag === 4;
}
function Bu(e) {
  e: for (; ; ) {
    for (; e.sibling === null; ) {
      if (e.return === null || lc(e.return)) return null;
      e = e.return;
    }
    for (e.sibling.return = e.return, e = e.sibling; e.tag !== 5 && e.tag !== 6 && e.tag !== 18; ) {
      if (e.flags & 2 || e.child === null || e.tag === 4) continue e;
      e.child.return = e, e = e.child;
    }
    if (!(e.flags & 2)) return e.stateNode;
  }
}
function Vi(e, t, n) {
  var r = e.tag;
  if (r === 5 || r === 6) e = e.stateNode, t ? n.nodeType === 8 ? n.parentNode.insertBefore(e, t) : n.insertBefore(e, t) : (n.nodeType === 8 ? (t = n.parentNode, t.insertBefore(e, n)) : (t = n, t.appendChild(e)), n = n._reactRootContainer, n != null || t.onclick !== null || (t.onclick = Wr));
  else if (r !== 4 && (e = e.child, e !== null)) for (Vi(e, t, n), e = e.sibling; e !== null; ) Vi(e, t, n), e = e.sibling;
}
function $i(e, t, n) {
  var r = e.tag;
  if (r === 5 || r === 6) e = e.stateNode, t ? n.insertBefore(e, t) : n.appendChild(e);
  else if (r !== 4 && (e = e.child, e !== null)) for ($i(e, t, n), e = e.sibling; e !== null; ) $i(e, t, n), e = e.sibling;
}
var ne = null, Ie = !1;
function et(e, t, n) {
  for (n = n.child; n !== null; ) ic(e, t, n), n = n.sibling;
}
function ic(e, t, n) {
  if (Be && typeof Be.onCommitFiberUnmount == "function") try {
    Be.onCommitFiberUnmount(sl, n);
  } catch {
  }
  switch (n.tag) {
    case 5:
      ue || Yt(n, t);
    case 6:
      var r = ne, l = Ie;
      ne = null, et(e, t, n), ne = r, Ie = l, ne !== null && (Ie ? (e = ne, n = n.stateNode, e.nodeType === 8 ? e.parentNode.removeChild(n) : e.removeChild(n)) : ne.removeChild(n.stateNode));
      break;
    case 18:
      ne !== null && (Ie ? (e = ne, n = n.stateNode, e.nodeType === 8 ? $l(e.parentNode, n) : e.nodeType === 1 && $l(e, n), $n(e)) : $l(ne, n.stateNode));
      break;
    case 4:
      r = ne, l = Ie, ne = n.stateNode.containerInfo, Ie = !0, et(e, t, n), ne = r, Ie = l;
      break;
    case 0:
    case 11:
    case 14:
    case 15:
      if (!ue && (r = n.updateQueue, r !== null && (r = r.lastEffect, r !== null))) {
        l = r = r.next;
        do {
          var i = l, o = i.destroy;
          i = i.tag, o !== void 0 && (i & 2 || i & 4) && Ui(n, t, o), l = l.next;
        } while (l !== r);
      }
      et(e, t, n);
      break;
    case 1:
      if (!ue && (Yt(n, t), r = n.stateNode, typeof r.componentWillUnmount == "function")) try {
        r.props = n.memoizedProps, r.state = n.memoizedState, r.componentWillUnmount();
      } catch (u) {
        Y(n, t, u);
      }
      et(e, t, n);
      break;
    case 21:
      et(e, t, n);
      break;
    case 22:
      n.mode & 1 ? (ue = (r = ue) || n.memoizedState !== null, et(e, t, n), ue = r) : et(e, t, n);
      break;
    default:
      et(e, t, n);
  }
}
function Hu(e) {
  var t = e.updateQueue;
  if (t !== null) {
    e.updateQueue = null;
    var n = e.stateNode;
    n === null && (n = e.stateNode = new Dd()), t.forEach(function(r) {
      var l = Wd.bind(null, e, r);
      n.has(r) || (n.add(r), r.then(l, l));
    });
  }
}
function Re(e, t) {
  var n = t.deletions;
  if (n !== null) for (var r = 0; r < n.length; r++) {
    var l = n[r];
    try {
      var i = e, o = t, u = o;
      e: for (; u !== null; ) {
        switch (u.tag) {
          case 5:
            ne = u.stateNode, Ie = !1;
            break e;
          case 3:
            ne = u.stateNode.containerInfo, Ie = !0;
            break e;
          case 4:
            ne = u.stateNode.containerInfo, Ie = !0;
            break e;
        }
        u = u.return;
      }
      if (ne === null) throw Error(S(160));
      ic(i, o, l), ne = null, Ie = !1;
      var s = l.alternate;
      s !== null && (s.return = null), l.return = null;
    } catch (c) {
      Y(l, t, c);
    }
  }
  if (t.subtreeFlags & 12854) for (t = t.child; t !== null; ) oc(t, e), t = t.sibling;
}
function oc(e, t) {
  var n = e.alternate, r = e.flags;
  switch (e.tag) {
    case 0:
    case 11:
    case 14:
    case 15:
      if (Re(t, e), Ae(e), r & 4) {
        try {
          Mn(3, e, e.return), vl(3, e);
        } catch (E) {
          Y(e, e.return, E);
        }
        try {
          Mn(5, e, e.return);
        } catch (E) {
          Y(e, e.return, E);
        }
      }
      break;
    case 1:
      Re(t, e), Ae(e), r & 512 && n !== null && Yt(n, n.return);
      break;
    case 5:
      if (Re(t, e), Ae(e), r & 512 && n !== null && Yt(n, n.return), e.flags & 32) {
        var l = e.stateNode;
        try {
          Fn(l, "");
        } catch (E) {
          Y(e, e.return, E);
        }
      }
      if (r & 4 && (l = e.stateNode, l != null)) {
        var i = e.memoizedProps, o = n !== null ? n.memoizedProps : i, u = e.type, s = e.updateQueue;
        if (e.updateQueue = null, s !== null) try {
          u === "input" && i.type === "radio" && i.name != null && js(l, i), fi(u, o);
          var c = fi(u, i);
          for (o = 0; o < s.length; o += 2) {
            var h = s[o], v = s[o + 1];
            h === "style" ? Ms(l, v) : h === "dangerouslySetInnerHTML" ? zs(l, v) : h === "children" ? Fn(l, v) : qi(l, h, v, c);
          }
          switch (u) {
            case "input":
              oi(l, i);
              break;
            case "textarea":
              Ls(l, i);
              break;
            case "select":
              var f = l._wrapperState.wasMultiple;
              l._wrapperState.wasMultiple = !!i.multiple;
              var w = i.value;
              w != null ? Gt(l, !!i.multiple, w, !1) : f !== !!i.multiple && (i.defaultValue != null ? Gt(
                l,
                !!i.multiple,
                i.defaultValue,
                !0
              ) : Gt(l, !!i.multiple, i.multiple ? [] : "", !1));
          }
          l[Kn] = i;
        } catch (E) {
          Y(e, e.return, E);
        }
      }
      break;
    case 6:
      if (Re(t, e), Ae(e), r & 4) {
        if (e.stateNode === null) throw Error(S(162));
        l = e.stateNode, i = e.memoizedProps;
        try {
          l.nodeValue = i;
        } catch (E) {
          Y(e, e.return, E);
        }
      }
      break;
    case 3:
      if (Re(t, e), Ae(e), r & 4 && n !== null && n.memoizedState.isDehydrated) try {
        $n(t.containerInfo);
      } catch (E) {
        Y(e, e.return, E);
      }
      break;
    case 4:
      Re(t, e), Ae(e);
      break;
    case 13:
      Re(t, e), Ae(e), l = e.child, l.flags & 8192 && (i = l.memoizedState !== null, l.stateNode.isHidden = i, !i || l.alternate !== null && l.alternate.memoizedState !== null || (Ro = G())), r & 4 && Hu(e);
      break;
    case 22:
      if (h = n !== null && n.memoizedState !== null, e.mode & 1 ? (ue = (c = ue) || h, Re(t, e), ue = c) : Re(t, e), Ae(e), r & 8192) {
        if (c = e.memoizedState !== null, (e.stateNode.isHidden = c) && !h && e.mode & 1) for (L = e, h = e.child; h !== null; ) {
          for (v = L = h; L !== null; ) {
            switch (f = L, w = f.child, f.tag) {
              case 0:
              case 11:
              case 14:
              case 15:
                Mn(4, f, f.return);
                break;
              case 1:
                Yt(f, f.return);
                var k = f.stateNode;
                if (typeof k.componentWillUnmount == "function") {
                  r = f, n = f.return;
                  try {
                    t = r, k.props = t.memoizedProps, k.state = t.memoizedState, k.componentWillUnmount();
                  } catch (E) {
                    Y(r, n, E);
                  }
                }
                break;
              case 5:
                Yt(f, f.return);
                break;
              case 22:
                if (f.memoizedState !== null) {
                  Wu(v);
                  continue;
                }
            }
            w !== null ? (w.return = f, L = w) : Wu(v);
          }
          h = h.sibling;
        }
        e: for (h = null, v = e; ; ) {
          if (v.tag === 5) {
            if (h === null) {
              h = v;
              try {
                l = v.stateNode, c ? (i = l.style, typeof i.setProperty == "function" ? i.setProperty("display", "none", "important") : i.display = "none") : (u = v.stateNode, s = v.memoizedProps.style, o = s != null && s.hasOwnProperty("display") ? s.display : null, u.style.display = Rs("display", o));
              } catch (E) {
                Y(e, e.return, E);
              }
            }
          } else if (v.tag === 6) {
            if (h === null) try {
              v.stateNode.nodeValue = c ? "" : v.memoizedProps;
            } catch (E) {
              Y(e, e.return, E);
            }
          } else if ((v.tag !== 22 && v.tag !== 23 || v.memoizedState === null || v === e) && v.child !== null) {
            v.child.return = v, v = v.child;
            continue;
          }
          if (v === e) break e;
          for (; v.sibling === null; ) {
            if (v.return === null || v.return === e) break e;
            h === v && (h = null), v = v.return;
          }
          h === v && (h = null), v.sibling.return = v.return, v = v.sibling;
        }
      }
      break;
    case 19:
      Re(t, e), Ae(e), r & 4 && Hu(e);
      break;
    case 21:
      break;
    default:
      Re(
        t,
        e
      ), Ae(e);
  }
}
function Ae(e) {
  var t = e.flags;
  if (t & 2) {
    try {
      e: {
        for (var n = e.return; n !== null; ) {
          if (lc(n)) {
            var r = n;
            break e;
          }
          n = n.return;
        }
        throw Error(S(160));
      }
      switch (r.tag) {
        case 5:
          var l = r.stateNode;
          r.flags & 32 && (Fn(l, ""), r.flags &= -33);
          var i = Bu(e);
          $i(e, i, l);
          break;
        case 3:
        case 4:
          var o = r.stateNode.containerInfo, u = Bu(e);
          Vi(e, u, o);
          break;
        default:
          throw Error(S(161));
      }
    } catch (s) {
      Y(e, e.return, s);
    }
    e.flags &= -3;
  }
  t & 4096 && (e.flags &= -4097);
}
function Fd(e, t, n) {
  L = e, uc(e);
}
function uc(e, t, n) {
  for (var r = (e.mode & 1) !== 0; L !== null; ) {
    var l = L, i = l.child;
    if (l.tag === 22 && r) {
      var o = l.memoizedState !== null || Sr;
      if (!o) {
        var u = l.alternate, s = u !== null && u.memoizedState !== null || ue;
        u = Sr;
        var c = ue;
        if (Sr = o, (ue = s) && !c) for (L = l; L !== null; ) o = L, s = o.child, o.tag === 22 && o.memoizedState !== null ? Ku(l) : s !== null ? (s.return = o, L = s) : Ku(l);
        for (; i !== null; ) L = i, uc(i), i = i.sibling;
        L = l, Sr = u, ue = c;
      }
      Qu(e);
    } else l.subtreeFlags & 8772 && i !== null ? (i.return = l, L = i) : Qu(e);
  }
}
function Qu(e) {
  for (; L !== null; ) {
    var t = L;
    if (t.flags & 8772) {
      var n = t.alternate;
      try {
        if (t.flags & 8772) switch (t.tag) {
          case 0:
          case 11:
          case 15:
            ue || vl(5, t);
            break;
          case 1:
            var r = t.stateNode;
            if (t.flags & 4 && !ue) if (n === null) r.componentDidMount();
            else {
              var l = t.elementType === t.type ? n.memoizedProps : Me(t.type, n.memoizedProps);
              r.componentDidUpdate(l, n.memoizedState, r.__reactInternalSnapshotBeforeUpdate);
            }
            var i = t.updateQueue;
            i !== null && ju(t, i, r);
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
              ju(t, o, n);
            }
            break;
          case 5:
            var u = t.stateNode;
            if (n === null && t.flags & 4) {
              n = u;
              var s = t.memoizedProps;
              switch (t.type) {
                case "button":
                case "input":
                case "select":
                case "textarea":
                  s.autoFocus && n.focus();
                  break;
                case "img":
                  s.src && (n.src = s.src);
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
              var c = t.alternate;
              if (c !== null) {
                var h = c.memoizedState;
                if (h !== null) {
                  var v = h.dehydrated;
                  v !== null && $n(v);
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
            throw Error(S(163));
        }
        ue || t.flags & 512 && Ai(t);
      } catch (f) {
        Y(t, t.return, f);
      }
    }
    if (t === e) {
      L = null;
      break;
    }
    if (n = t.sibling, n !== null) {
      n.return = t.return, L = n;
      break;
    }
    L = t.return;
  }
}
function Wu(e) {
  for (; L !== null; ) {
    var t = L;
    if (t === e) {
      L = null;
      break;
    }
    var n = t.sibling;
    if (n !== null) {
      n.return = t.return, L = n;
      break;
    }
    L = t.return;
  }
}
function Ku(e) {
  for (; L !== null; ) {
    var t = L;
    try {
      switch (t.tag) {
        case 0:
        case 11:
        case 15:
          var n = t.return;
          try {
            vl(4, t);
          } catch (s) {
            Y(t, n, s);
          }
          break;
        case 1:
          var r = t.stateNode;
          if (typeof r.componentDidMount == "function") {
            var l = t.return;
            try {
              r.componentDidMount();
            } catch (s) {
              Y(t, l, s);
            }
          }
          var i = t.return;
          try {
            Ai(t);
          } catch (s) {
            Y(t, i, s);
          }
          break;
        case 5:
          var o = t.return;
          try {
            Ai(t);
          } catch (s) {
            Y(t, o, s);
          }
      }
    } catch (s) {
      Y(t, t.return, s);
    }
    if (t === e) {
      L = null;
      break;
    }
    var u = t.sibling;
    if (u !== null) {
      u.return = t.return, L = u;
      break;
    }
    L = t.return;
  }
}
var Ud = Math.ceil, nl = be.ReactCurrentDispatcher, To = be.ReactCurrentOwner, Le = be.ReactCurrentBatchConfig, O = 0, te = null, Z = null, re = 0, we = 0, Xt = yt(0), q = 0, qn = null, Rt = 0, gl = 0, zo = 0, In = null, me = null, Ro = 0, sn = 1 / 0, Qe = null, rl = !1, Bi = null, dt = null, xr = !1, ot = null, ll = 0, Dn = 0, Hi = null, Mr = -1, Ir = 0;
function fe() {
  return O & 6 ? G() : Mr !== -1 ? Mr : Mr = G();
}
function pt(e) {
  return e.mode & 1 ? O & 2 && re !== 0 ? re & -re : Sd.transition !== null ? (Ir === 0 && (Ir = Ws()), Ir) : (e = F, e !== 0 || (e = window.event, e = e === void 0 ? 16 : qs(e.type)), e) : 1;
}
function Fe(e, t, n, r) {
  if (50 < Dn) throw Dn = 0, Hi = null, Error(S(185));
  er(e, n, r), (!(O & 2) || e !== te) && (e === te && (!(O & 2) && (gl |= n), q === 4 && lt(e, re)), ye(e, r), n === 1 && O === 0 && !(t.mode & 1) && (sn = G() + 500, pl && wt()));
}
function ye(e, t) {
  var n = e.callbackNode;
  Sf(e, t);
  var r = $r(e, e === te ? re : 0);
  if (r === 0) n !== null && tu(n), e.callbackNode = null, e.callbackPriority = 0;
  else if (t = r & -r, e.callbackPriority !== t) {
    if (n != null && tu(n), t === 1) e.tag === 0 ? kd(Yu.bind(null, e)) : ga(Yu.bind(null, e)), vd(function() {
      !(O & 6) && wt();
    }), n = null;
    else {
      switch (Ks(r)) {
        case 1:
          n = ro;
          break;
        case 4:
          n = Hs;
          break;
        case 16:
          n = Vr;
          break;
        case 536870912:
          n = Qs;
          break;
        default:
          n = Vr;
      }
      n = hc(n, sc.bind(null, e));
    }
    e.callbackPriority = t, e.callbackNode = n;
  }
}
function sc(e, t) {
  if (Mr = -1, Ir = 0, O & 6) throw Error(S(327));
  var n = e.callbackNode;
  if (en() && e.callbackNode !== n) return null;
  var r = $r(e, e === te ? re : 0);
  if (r === 0) return null;
  if (r & 30 || r & e.expiredLanes || t) t = il(e, r);
  else {
    t = r;
    var l = O;
    O |= 2;
    var i = cc();
    (te !== e || re !== t) && (Qe = null, sn = G() + 500, Pt(e, t));
    do
      try {
        $d();
        break;
      } catch (u) {
        ac(e, u);
      }
    while (!0);
    go(), nl.current = i, O = l, Z !== null ? t = 0 : (te = null, re = 0, t = q);
  }
  if (t !== 0) {
    if (t === 2 && (l = vi(e), l !== 0 && (r = l, t = Qi(e, l))), t === 1) throw n = qn, Pt(e, 0), lt(e, r), ye(e, G()), n;
    if (t === 6) lt(e, r);
    else {
      if (l = e.current.alternate, !(r & 30) && !Ad(l) && (t = il(e, r), t === 2 && (i = vi(e), i !== 0 && (r = i, t = Qi(e, i))), t === 1)) throw n = qn, Pt(e, 0), lt(e, r), ye(e, G()), n;
      switch (e.finishedWork = l, e.finishedLanes = r, t) {
        case 0:
        case 1:
          throw Error(S(345));
        case 2:
          Et(e, me, Qe);
          break;
        case 3:
          if (lt(e, r), (r & 130023424) === r && (t = Ro + 500 - G(), 10 < t)) {
            if ($r(e, 0) !== 0) break;
            if (l = e.suspendedLanes, (l & r) !== r) {
              fe(), e.pingedLanes |= e.suspendedLanes & l;
              break;
            }
            e.timeoutHandle = Ci(Et.bind(null, e, me, Qe), t);
            break;
          }
          Et(e, me, Qe);
          break;
        case 4:
          if (lt(e, r), (r & 4194240) === r) break;
          for (t = e.eventTimes, l = -1; 0 < r; ) {
            var o = 31 - Oe(r);
            i = 1 << o, o = t[o], o > l && (l = o), r &= ~i;
          }
          if (r = l, r = G() - r, r = (120 > r ? 120 : 480 > r ? 480 : 1080 > r ? 1080 : 1920 > r ? 1920 : 3e3 > r ? 3e3 : 4320 > r ? 4320 : 1960 * Ud(r / 1960)) - r, 10 < r) {
            e.timeoutHandle = Ci(Et.bind(null, e, me, Qe), r);
            break;
          }
          Et(e, me, Qe);
          break;
        case 5:
          Et(e, me, Qe);
          break;
        default:
          throw Error(S(329));
      }
    }
  }
  return ye(e, G()), e.callbackNode === n ? sc.bind(null, e) : null;
}
function Qi(e, t) {
  var n = In;
  return e.current.memoizedState.isDehydrated && (Pt(e, t).flags |= 256), e = il(e, t), e !== 2 && (t = me, me = n, t !== null && Wi(t)), e;
}
function Wi(e) {
  me === null ? me = e : me.push.apply(me, e);
}
function Ad(e) {
  for (var t = e; ; ) {
    if (t.flags & 16384) {
      var n = t.updateQueue;
      if (n !== null && (n = n.stores, n !== null)) for (var r = 0; r < n.length; r++) {
        var l = n[r], i = l.getSnapshot;
        l = l.value;
        try {
          if (!Ue(i(), l)) return !1;
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
function lt(e, t) {
  for (t &= ~zo, t &= ~gl, e.suspendedLanes |= t, e.pingedLanes &= ~t, e = e.expirationTimes; 0 < t; ) {
    var n = 31 - Oe(t), r = 1 << n;
    e[n] = -1, t &= ~r;
  }
}
function Yu(e) {
  if (O & 6) throw Error(S(327));
  en();
  var t = $r(e, 0);
  if (!(t & 1)) return ye(e, G()), null;
  var n = il(e, t);
  if (e.tag !== 0 && n === 2) {
    var r = vi(e);
    r !== 0 && (t = r, n = Qi(e, r));
  }
  if (n === 1) throw n = qn, Pt(e, 0), lt(e, t), ye(e, G()), n;
  if (n === 6) throw Error(S(345));
  return e.finishedWork = e.current.alternate, e.finishedLanes = t, Et(e, me, Qe), ye(e, G()), null;
}
function Mo(e, t) {
  var n = O;
  O |= 1;
  try {
    return e(t);
  } finally {
    O = n, O === 0 && (sn = G() + 500, pl && wt());
  }
}
function Mt(e) {
  ot !== null && ot.tag === 0 && !(O & 6) && en();
  var t = O;
  O |= 1;
  var n = Le.transition, r = F;
  try {
    if (Le.transition = null, F = 1, e) return e();
  } finally {
    F = r, Le.transition = n, O = t, !(O & 6) && wt();
  }
}
function Io() {
  we = Xt.current, $(Xt);
}
function Pt(e, t) {
  e.finishedWork = null, e.finishedLanes = 0;
  var n = e.timeoutHandle;
  if (n !== -1 && (e.timeoutHandle = -1, hd(n)), Z !== null) for (n = Z.return; n !== null; ) {
    var r = n;
    switch (mo(r), r.tag) {
      case 1:
        r = r.type.childContextTypes, r != null && Kr();
        break;
      case 3:
        on(), $(ve), $(se), Eo();
        break;
      case 5:
        xo(r);
        break;
      case 4:
        on();
        break;
      case 13:
        $(Q);
        break;
      case 19:
        $(Q);
        break;
      case 10:
        yo(r.type._context);
        break;
      case 22:
      case 23:
        Io();
    }
    n = n.return;
  }
  if (te = e, Z = e = mt(e.current, null), re = we = t, q = 0, qn = null, zo = gl = Rt = 0, me = In = null, _t !== null) {
    for (t = 0; t < _t.length; t++) if (n = _t[t], r = n.interleaved, r !== null) {
      n.interleaved = null;
      var l = r.next, i = n.pending;
      if (i !== null) {
        var o = i.next;
        i.next = l, r.next = o;
      }
      n.pending = r;
    }
    _t = null;
  }
  return e;
}
function ac(e, t) {
  do {
    var n = Z;
    try {
      if (go(), Tr.current = tl, el) {
        for (var r = W.memoizedState; r !== null; ) {
          var l = r.queue;
          l !== null && (l.pending = null), r = r.next;
        }
        el = !1;
      }
      if (zt = 0, ee = J = W = null, Rn = !1, Gn = 0, To.current = null, n === null || n.return === null) {
        q = 1, qn = t, Z = null;
        break;
      }
      e: {
        var i = e, o = n.return, u = n, s = t;
        if (t = re, u.flags |= 32768, s !== null && typeof s == "object" && typeof s.then == "function") {
          var c = s, h = u, v = h.tag;
          if (!(h.mode & 1) && (v === 0 || v === 11 || v === 15)) {
            var f = h.alternate;
            f ? (h.updateQueue = f.updateQueue, h.memoizedState = f.memoizedState, h.lanes = f.lanes) : (h.updateQueue = null, h.memoizedState = null);
          }
          var w = Iu(o);
          if (w !== null) {
            w.flags &= -257, Du(w, o, u, i, t), w.mode & 1 && Mu(i, c, t), t = w, s = c;
            var k = t.updateQueue;
            if (k === null) {
              var E = /* @__PURE__ */ new Set();
              E.add(s), t.updateQueue = E;
            } else k.add(s);
            break e;
          } else {
            if (!(t & 1)) {
              Mu(i, c, t), Do();
              break e;
            }
            s = Error(S(426));
          }
        } else if (H && u.mode & 1) {
          var M = Iu(o);
          if (M !== null) {
            !(M.flags & 65536) && (M.flags |= 256), Du(M, o, u, i, t), ho(un(s, u));
            break e;
          }
        }
        i = s = un(s, u), q !== 4 && (q = 2), In === null ? In = [i] : In.push(i), i = o;
        do {
          switch (i.tag) {
            case 3:
              i.flags |= 65536, t &= -t, i.lanes |= t;
              var d = Ka(i, s, t);
              Pu(i, d);
              break e;
            case 1:
              u = s;
              var a = i.type, p = i.stateNode;
              if (!(i.flags & 128) && (typeof a.getDerivedStateFromError == "function" || p !== null && typeof p.componentDidCatch == "function" && (dt === null || !dt.has(p)))) {
                i.flags |= 65536, t &= -t, i.lanes |= t;
                var g = Ya(i, u, t);
                Pu(i, g);
                break e;
              }
          }
          i = i.return;
        } while (i !== null);
      }
      dc(n);
    } catch (C) {
      t = C, Z === n && n !== null && (Z = n = n.return);
      continue;
    }
    break;
  } while (!0);
}
function cc() {
  var e = nl.current;
  return nl.current = tl, e === null ? tl : e;
}
function Do() {
  (q === 0 || q === 3 || q === 2) && (q = 4), te === null || !(Rt & 268435455) && !(gl & 268435455) || lt(te, re);
}
function il(e, t) {
  var n = O;
  O |= 2;
  var r = cc();
  (te !== e || re !== t) && (Qe = null, Pt(e, t));
  do
    try {
      Vd();
      break;
    } catch (l) {
      ac(e, l);
    }
  while (!0);
  if (go(), O = n, nl.current = r, Z !== null) throw Error(S(261));
  return te = null, re = 0, q;
}
function Vd() {
  for (; Z !== null; ) fc(Z);
}
function $d() {
  for (; Z !== null && !df(); ) fc(Z);
}
function fc(e) {
  var t = mc(e.alternate, e, we);
  e.memoizedProps = e.pendingProps, t === null ? dc(e) : Z = t, To.current = null;
}
function dc(e) {
  var t = e;
  do {
    var n = t.alternate;
    if (e = t.return, t.flags & 32768) {
      if (n = Id(n, t), n !== null) {
        n.flags &= 32767, Z = n;
        return;
      }
      if (e !== null) e.flags |= 32768, e.subtreeFlags = 0, e.deletions = null;
      else {
        q = 6, Z = null;
        return;
      }
    } else if (n = Md(n, t, we), n !== null) {
      Z = n;
      return;
    }
    if (t = t.sibling, t !== null) {
      Z = t;
      return;
    }
    Z = t = e;
  } while (t !== null);
  q === 0 && (q = 5);
}
function Et(e, t, n) {
  var r = F, l = Le.transition;
  try {
    Le.transition = null, F = 1, Bd(e, t, n, r);
  } finally {
    Le.transition = l, F = r;
  }
  return null;
}
function Bd(e, t, n, r) {
  do
    en();
  while (ot !== null);
  if (O & 6) throw Error(S(327));
  n = e.finishedWork;
  var l = e.finishedLanes;
  if (n === null) return null;
  if (e.finishedWork = null, e.finishedLanes = 0, n === e.current) throw Error(S(177));
  e.callbackNode = null, e.callbackPriority = 0;
  var i = n.lanes | n.childLanes;
  if (xf(e, i), e === te && (Z = te = null, re = 0), !(n.subtreeFlags & 2064) && !(n.flags & 2064) || xr || (xr = !0, hc(Vr, function() {
    return en(), null;
  })), i = (n.flags & 15990) !== 0, n.subtreeFlags & 15990 || i) {
    i = Le.transition, Le.transition = null;
    var o = F;
    F = 1;
    var u = O;
    O |= 4, To.current = null, Od(e, n), oc(n, e), sd(xi), Br = !!Si, xi = Si = null, e.current = n, Fd(n), pf(), O = u, F = o, Le.transition = i;
  } else e.current = n;
  if (xr && (xr = !1, ot = e, ll = l), i = e.pendingLanes, i === 0 && (dt = null), vf(n.stateNode), ye(e, G()), t !== null) for (r = e.onRecoverableError, n = 0; n < t.length; n++) l = t[n], r(l.value, { componentStack: l.stack, digest: l.digest });
  if (rl) throw rl = !1, e = Bi, Bi = null, e;
  return ll & 1 && e.tag !== 0 && en(), i = e.pendingLanes, i & 1 ? e === Hi ? Dn++ : (Dn = 0, Hi = e) : Dn = 0, wt(), null;
}
function en() {
  if (ot !== null) {
    var e = Ks(ll), t = Le.transition, n = F;
    try {
      if (Le.transition = null, F = 16 > e ? 16 : e, ot === null) var r = !1;
      else {
        if (e = ot, ot = null, ll = 0, O & 6) throw Error(S(331));
        var l = O;
        for (O |= 4, L = e.current; L !== null; ) {
          var i = L, o = i.child;
          if (L.flags & 16) {
            var u = i.deletions;
            if (u !== null) {
              for (var s = 0; s < u.length; s++) {
                var c = u[s];
                for (L = c; L !== null; ) {
                  var h = L;
                  switch (h.tag) {
                    case 0:
                    case 11:
                    case 15:
                      Mn(8, h, i);
                  }
                  var v = h.child;
                  if (v !== null) v.return = h, L = v;
                  else for (; L !== null; ) {
                    h = L;
                    var f = h.sibling, w = h.return;
                    if (rc(h), h === c) {
                      L = null;
                      break;
                    }
                    if (f !== null) {
                      f.return = w, L = f;
                      break;
                    }
                    L = w;
                  }
                }
              }
              var k = i.alternate;
              if (k !== null) {
                var E = k.child;
                if (E !== null) {
                  k.child = null;
                  do {
                    var M = E.sibling;
                    E.sibling = null, E = M;
                  } while (E !== null);
                }
              }
              L = i;
            }
          }
          if (i.subtreeFlags & 2064 && o !== null) o.return = i, L = o;
          else e: for (; L !== null; ) {
            if (i = L, i.flags & 2048) switch (i.tag) {
              case 0:
              case 11:
              case 15:
                Mn(9, i, i.return);
            }
            var d = i.sibling;
            if (d !== null) {
              d.return = i.return, L = d;
              break e;
            }
            L = i.return;
          }
        }
        var a = e.current;
        for (L = a; L !== null; ) {
          o = L;
          var p = o.child;
          if (o.subtreeFlags & 2064 && p !== null) p.return = o, L = p;
          else e: for (o = a; L !== null; ) {
            if (u = L, u.flags & 2048) try {
              switch (u.tag) {
                case 0:
                case 11:
                case 15:
                  vl(9, u);
              }
            } catch (C) {
              Y(u, u.return, C);
            }
            if (u === o) {
              L = null;
              break e;
            }
            var g = u.sibling;
            if (g !== null) {
              g.return = u.return, L = g;
              break e;
            }
            L = u.return;
          }
        }
        if (O = l, wt(), Be && typeof Be.onPostCommitFiberRoot == "function") try {
          Be.onPostCommitFiberRoot(sl, e);
        } catch {
        }
        r = !0;
      }
      return r;
    } finally {
      F = n, Le.transition = t;
    }
  }
  return !1;
}
function Xu(e, t, n) {
  t = un(n, t), t = Ka(e, t, 1), e = ft(e, t, 1), t = fe(), e !== null && (er(e, 1, t), ye(e, t));
}
function Y(e, t, n) {
  if (e.tag === 3) Xu(e, e, n);
  else for (; t !== null; ) {
    if (t.tag === 3) {
      Xu(t, e, n);
      break;
    } else if (t.tag === 1) {
      var r = t.stateNode;
      if (typeof t.type.getDerivedStateFromError == "function" || typeof r.componentDidCatch == "function" && (dt === null || !dt.has(r))) {
        e = un(n, e), e = Ya(t, e, 1), t = ft(t, e, 1), e = fe(), t !== null && (er(t, 1, e), ye(t, e));
        break;
      }
    }
    t = t.return;
  }
}
function Hd(e, t, n) {
  var r = e.pingCache;
  r !== null && r.delete(t), t = fe(), e.pingedLanes |= e.suspendedLanes & n, te === e && (re & n) === n && (q === 4 || q === 3 && (re & 130023424) === re && 500 > G() - Ro ? Pt(e, 0) : zo |= n), ye(e, t);
}
function pc(e, t) {
  t === 0 && (e.mode & 1 ? (t = dr, dr <<= 1, !(dr & 130023424) && (dr = 4194304)) : t = 1);
  var n = fe();
  e = Je(e, t), e !== null && (er(e, t, n), ye(e, n));
}
function Qd(e) {
  var t = e.memoizedState, n = 0;
  t !== null && (n = t.retryLane), pc(e, n);
}
function Wd(e, t) {
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
      throw Error(S(314));
  }
  r !== null && r.delete(t), pc(e, n);
}
var mc;
mc = function(e, t, n) {
  if (e !== null) if (e.memoizedProps !== t.pendingProps || ve.current) he = !0;
  else {
    if (!(e.lanes & n) && !(t.flags & 128)) return he = !1, Rd(e, t, n);
    he = !!(e.flags & 131072);
  }
  else he = !1, H && t.flags & 1048576 && ya(t, Gr, t.index);
  switch (t.lanes = 0, t.tag) {
    case 2:
      var r = t.type;
      Rr(e, t), e = t.pendingProps;
      var l = nn(t, se.current);
      bt(t, n), l = _o(null, t, r, e, l, n);
      var i = No();
      return t.flags |= 1, typeof l == "object" && l !== null && typeof l.render == "function" && l.$$typeof === void 0 ? (t.tag = 1, t.memoizedState = null, t.updateQueue = null, ge(r) ? (i = !0, Yr(t)) : i = !1, t.memoizedState = l.state !== null && l.state !== void 0 ? l.state : null, ko(t), l.updater = hl, t.stateNode = l, l._reactInternals = t, zi(t, r, e, n), t = Ii(null, t, r, !0, i, n)) : (t.tag = 0, H && i && po(t), ce(null, t, l, n), t = t.child), t;
    case 16:
      r = t.elementType;
      e: {
        switch (Rr(e, t), e = t.pendingProps, l = r._init, r = l(r._payload), t.type = r, l = t.tag = Yd(r), e = Me(r, e), l) {
          case 0:
            t = Mi(null, t, r, e, n);
            break e;
          case 1:
            t = Uu(null, t, r, e, n);
            break e;
          case 11:
            t = Ou(null, t, r, e, n);
            break e;
          case 14:
            t = Fu(null, t, r, Me(r.type, e), n);
            break e;
        }
        throw Error(S(
          306,
          r,
          ""
        ));
      }
      return t;
    case 0:
      return r = t.type, l = t.pendingProps, l = t.elementType === r ? l : Me(r, l), Mi(e, t, r, l, n);
    case 1:
      return r = t.type, l = t.pendingProps, l = t.elementType === r ? l : Me(r, l), Uu(e, t, r, l, n);
    case 3:
      e: {
        if (Ja(t), e === null) throw Error(S(387));
        r = t.pendingProps, i = t.memoizedState, l = i.element, Ca(e, t), qr(t, r, null, n);
        var o = t.memoizedState;
        if (r = o.element, i.isDehydrated) if (i = { element: r, isDehydrated: !1, cache: o.cache, pendingSuspenseBoundaries: o.pendingSuspenseBoundaries, transitions: o.transitions }, t.updateQueue.baseState = i, t.memoizedState = i, t.flags & 256) {
          l = un(Error(S(423)), t), t = Au(e, t, r, n, l);
          break e;
        } else if (r !== l) {
          l = un(Error(S(424)), t), t = Au(e, t, r, n, l);
          break e;
        } else for (ke = ct(t.stateNode.containerInfo.firstChild), Se = t, H = !0, De = null, n = xa(t, null, r, n), t.child = n; n; ) n.flags = n.flags & -3 | 4096, n = n.sibling;
        else {
          if (rn(), r === l) {
            t = qe(e, t, n);
            break e;
          }
          ce(e, t, r, n);
        }
        t = t.child;
      }
      return t;
    case 5:
      return _a(t), e === null && ji(t), r = t.type, l = t.pendingProps, i = e !== null ? e.memoizedProps : null, o = l.children, Ei(r, l) ? o = null : i !== null && Ei(r, i) && (t.flags |= 32), Za(e, t), ce(e, t, o, n), t.child;
    case 6:
      return e === null && ji(t), null;
    case 13:
      return qa(e, t, n);
    case 4:
      return So(t, t.stateNode.containerInfo), r = t.pendingProps, e === null ? t.child = ln(t, null, r, n) : ce(e, t, r, n), t.child;
    case 11:
      return r = t.type, l = t.pendingProps, l = t.elementType === r ? l : Me(r, l), Ou(e, t, r, l, n);
    case 7:
      return ce(e, t, t.pendingProps, n), t.child;
    case 8:
      return ce(e, t, t.pendingProps.children, n), t.child;
    case 12:
      return ce(e, t, t.pendingProps.children, n), t.child;
    case 10:
      e: {
        if (r = t.type._context, l = t.pendingProps, i = t.memoizedProps, o = l.value, U(Zr, r._currentValue), r._currentValue = o, i !== null) if (Ue(i.value, o)) {
          if (i.children === l.children && !ve.current) {
            t = qe(e, t, n);
            break e;
          }
        } else for (i = t.child, i !== null && (i.return = t); i !== null; ) {
          var u = i.dependencies;
          if (u !== null) {
            o = i.child;
            for (var s = u.firstContext; s !== null; ) {
              if (s.context === r) {
                if (i.tag === 1) {
                  s = Xe(-1, n & -n), s.tag = 2;
                  var c = i.updateQueue;
                  if (c !== null) {
                    c = c.shared;
                    var h = c.pending;
                    h === null ? s.next = s : (s.next = h.next, h.next = s), c.pending = s;
                  }
                }
                i.lanes |= n, s = i.alternate, s !== null && (s.lanes |= n), Li(
                  i.return,
                  n,
                  t
                ), u.lanes |= n;
                break;
              }
              s = s.next;
            }
          } else if (i.tag === 10) o = i.type === t.type ? null : i.child;
          else if (i.tag === 18) {
            if (o = i.return, o === null) throw Error(S(341));
            o.lanes |= n, u = o.alternate, u !== null && (u.lanes |= n), Li(o, n, t), o = i.sibling;
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
        ce(e, t, l.children, n), t = t.child;
      }
      return t;
    case 9:
      return l = t.type, r = t.pendingProps.children, bt(t, n), l = Te(l), r = r(l), t.flags |= 1, ce(e, t, r, n), t.child;
    case 14:
      return r = t.type, l = Me(r, t.pendingProps), l = Me(r.type, l), Fu(e, t, r, l, n);
    case 15:
      return Xa(e, t, t.type, t.pendingProps, n);
    case 17:
      return r = t.type, l = t.pendingProps, l = t.elementType === r ? l : Me(r, l), Rr(e, t), t.tag = 1, ge(r) ? (e = !0, Yr(t)) : e = !1, bt(t, n), Wa(t, r, l), zi(t, r, l, n), Ii(null, t, r, !0, e, n);
    case 19:
      return ba(e, t, n);
    case 22:
      return Ga(e, t, n);
  }
  throw Error(S(156, t.tag));
};
function hc(e, t) {
  return Bs(e, t);
}
function Kd(e, t, n, r) {
  this.tag = e, this.key = n, this.sibling = this.child = this.return = this.stateNode = this.type = this.elementType = null, this.index = 0, this.ref = null, this.pendingProps = t, this.dependencies = this.memoizedState = this.updateQueue = this.memoizedProps = null, this.mode = r, this.subtreeFlags = this.flags = 0, this.deletions = null, this.childLanes = this.lanes = 0, this.alternate = null;
}
function je(e, t, n, r) {
  return new Kd(e, t, n, r);
}
function Oo(e) {
  return e = e.prototype, !(!e || !e.isReactComponent);
}
function Yd(e) {
  if (typeof e == "function") return Oo(e) ? 1 : 0;
  if (e != null) {
    if (e = e.$$typeof, e === eo) return 11;
    if (e === to) return 14;
  }
  return 2;
}
function mt(e, t) {
  var n = e.alternate;
  return n === null ? (n = je(e.tag, t, e.key, e.mode), n.elementType = e.elementType, n.type = e.type, n.stateNode = e.stateNode, n.alternate = e, e.alternate = n) : (n.pendingProps = t, n.type = e.type, n.flags = 0, n.subtreeFlags = 0, n.deletions = null), n.flags = e.flags & 14680064, n.childLanes = e.childLanes, n.lanes = e.lanes, n.child = e.child, n.memoizedProps = e.memoizedProps, n.memoizedState = e.memoizedState, n.updateQueue = e.updateQueue, t = e.dependencies, n.dependencies = t === null ? null : { lanes: t.lanes, firstContext: t.firstContext }, n.sibling = e.sibling, n.index = e.index, n.ref = e.ref, n;
}
function Dr(e, t, n, r, l, i) {
  var o = 2;
  if (r = e, typeof e == "function") Oo(e) && (o = 1);
  else if (typeof e == "string") o = 5;
  else e: switch (e) {
    case Ut:
      return jt(n.children, l, i, t);
    case bi:
      o = 8, l |= 8;
      break;
    case ti:
      return e = je(12, n, t, l | 2), e.elementType = ti, e.lanes = i, e;
    case ni:
      return e = je(13, n, t, l), e.elementType = ni, e.lanes = i, e;
    case ri:
      return e = je(19, n, t, l), e.elementType = ri, e.lanes = i, e;
    case _s:
      return yl(n, l, i, t);
    default:
      if (typeof e == "object" && e !== null) switch (e.$$typeof) {
        case Es:
          o = 10;
          break e;
        case Cs:
          o = 9;
          break e;
        case eo:
          o = 11;
          break e;
        case to:
          o = 14;
          break e;
        case tt:
          o = 16, r = null;
          break e;
      }
      throw Error(S(130, e == null ? e : typeof e, ""));
  }
  return t = je(o, n, t, l), t.elementType = e, t.type = r, t.lanes = i, t;
}
function jt(e, t, n, r) {
  return e = je(7, e, r, t), e.lanes = n, e;
}
function yl(e, t, n, r) {
  return e = je(22, e, r, t), e.elementType = _s, e.lanes = n, e.stateNode = { isHidden: !1 }, e;
}
function Gl(e, t, n) {
  return e = je(6, e, null, t), e.lanes = n, e;
}
function Zl(e, t, n) {
  return t = je(4, e.children !== null ? e.children : [], e.key, t), t.lanes = n, t.stateNode = { containerInfo: e.containerInfo, pendingChildren: null, implementation: e.implementation }, t;
}
function Xd(e, t, n, r, l) {
  this.tag = t, this.containerInfo = e, this.finishedWork = this.pingCache = this.current = this.pendingChildren = null, this.timeoutHandle = -1, this.callbackNode = this.pendingContext = this.context = null, this.callbackPriority = 0, this.eventTimes = Tl(0), this.expirationTimes = Tl(-1), this.entangledLanes = this.finishedLanes = this.mutableReadLanes = this.expiredLanes = this.pingedLanes = this.suspendedLanes = this.pendingLanes = 0, this.entanglements = Tl(0), this.identifierPrefix = r, this.onRecoverableError = l, this.mutableSourceEagerHydrationData = null;
}
function Fo(e, t, n, r, l, i, o, u, s) {
  return e = new Xd(e, t, n, u, s), t === 1 ? (t = 1, i === !0 && (t |= 8)) : t = 0, i = je(3, null, null, t), e.current = i, i.stateNode = e, i.memoizedState = { element: r, isDehydrated: n, cache: null, transitions: null, pendingSuspenseBoundaries: null }, ko(i), e;
}
function Gd(e, t, n) {
  var r = 3 < arguments.length && arguments[3] !== void 0 ? arguments[3] : null;
  return { $$typeof: Ft, key: r == null ? null : "" + r, children: e, containerInfo: t, implementation: n };
}
function vc(e) {
  if (!e) return vt;
  e = e._reactInternals;
  e: {
    if (Dt(e) !== e || e.tag !== 1) throw Error(S(170));
    var t = e;
    do {
      switch (t.tag) {
        case 3:
          t = t.stateNode.context;
          break e;
        case 1:
          if (ge(t.type)) {
            t = t.stateNode.__reactInternalMemoizedMergedChildContext;
            break e;
          }
      }
      t = t.return;
    } while (t !== null);
    throw Error(S(171));
  }
  if (e.tag === 1) {
    var n = e.type;
    if (ge(n)) return va(e, n, t);
  }
  return t;
}
function gc(e, t, n, r, l, i, o, u, s) {
  return e = Fo(n, r, !0, e, l, i, o, u, s), e.context = vc(null), n = e.current, r = fe(), l = pt(n), i = Xe(r, l), i.callback = t ?? null, ft(n, i, l), e.current.lanes = l, er(e, l, r), ye(e, r), e;
}
function wl(e, t, n, r) {
  var l = t.current, i = fe(), o = pt(l);
  return n = vc(n), t.context === null ? t.context = n : t.pendingContext = n, t = Xe(i, o), t.payload = { element: e }, r = r === void 0 ? null : r, r !== null && (t.callback = r), e = ft(l, t, o), e !== null && (Fe(e, l, o, i), Lr(e, l, o)), o;
}
function ol(e) {
  if (e = e.current, !e.child) return null;
  switch (e.child.tag) {
    case 5:
      return e.child.stateNode;
    default:
      return e.child.stateNode;
  }
}
function Gu(e, t) {
  if (e = e.memoizedState, e !== null && e.dehydrated !== null) {
    var n = e.retryLane;
    e.retryLane = n !== 0 && n < t ? n : t;
  }
}
function Uo(e, t) {
  Gu(e, t), (e = e.alternate) && Gu(e, t);
}
function Zd() {
  return null;
}
var yc = typeof reportError == "function" ? reportError : function(e) {
  console.error(e);
};
function Ao(e) {
  this._internalRoot = e;
}
kl.prototype.render = Ao.prototype.render = function(e) {
  var t = this._internalRoot;
  if (t === null) throw Error(S(409));
  wl(e, t, null, null);
};
kl.prototype.unmount = Ao.prototype.unmount = function() {
  var e = this._internalRoot;
  if (e !== null) {
    this._internalRoot = null;
    var t = e.containerInfo;
    Mt(function() {
      wl(null, e, null, null);
    }), t[Ze] = null;
  }
};
function kl(e) {
  this._internalRoot = e;
}
kl.prototype.unstable_scheduleHydration = function(e) {
  if (e) {
    var t = Gs();
    e = { blockedOn: null, target: e, priority: t };
    for (var n = 0; n < rt.length && t !== 0 && t < rt[n].priority; n++) ;
    rt.splice(n, 0, e), n === 0 && Js(e);
  }
};
function Vo(e) {
  return !(!e || e.nodeType !== 1 && e.nodeType !== 9 && e.nodeType !== 11);
}
function Sl(e) {
  return !(!e || e.nodeType !== 1 && e.nodeType !== 9 && e.nodeType !== 11 && (e.nodeType !== 8 || e.nodeValue !== " react-mount-point-unstable "));
}
function Zu() {
}
function Jd(e, t, n, r, l) {
  if (l) {
    if (typeof r == "function") {
      var i = r;
      r = function() {
        var c = ol(o);
        i.call(c);
      };
    }
    var o = gc(t, r, e, 0, null, !1, !1, "", Zu);
    return e._reactRootContainer = o, e[Ze] = o.current, Qn(e.nodeType === 8 ? e.parentNode : e), Mt(), o;
  }
  for (; l = e.lastChild; ) e.removeChild(l);
  if (typeof r == "function") {
    var u = r;
    r = function() {
      var c = ol(s);
      u.call(c);
    };
  }
  var s = Fo(e, 0, !1, null, null, !1, !1, "", Zu);
  return e._reactRootContainer = s, e[Ze] = s.current, Qn(e.nodeType === 8 ? e.parentNode : e), Mt(function() {
    wl(t, s, n, r);
  }), s;
}
function xl(e, t, n, r, l) {
  var i = n._reactRootContainer;
  if (i) {
    var o = i;
    if (typeof l == "function") {
      var u = l;
      l = function() {
        var s = ol(o);
        u.call(s);
      };
    }
    wl(t, o, e, l);
  } else o = Jd(n, t, e, l, r);
  return ol(o);
}
Ys = function(e) {
  switch (e.tag) {
    case 3:
      var t = e.stateNode;
      if (t.current.memoizedState.isDehydrated) {
        var n = _n(t.pendingLanes);
        n !== 0 && (lo(t, n | 1), ye(t, G()), !(O & 6) && (sn = G() + 500, wt()));
      }
      break;
    case 13:
      Mt(function() {
        var r = Je(e, 1);
        if (r !== null) {
          var l = fe();
          Fe(r, e, 1, l);
        }
      }), Uo(e, 1);
  }
};
io = function(e) {
  if (e.tag === 13) {
    var t = Je(e, 134217728);
    if (t !== null) {
      var n = fe();
      Fe(t, e, 134217728, n);
    }
    Uo(e, 134217728);
  }
};
Xs = function(e) {
  if (e.tag === 13) {
    var t = pt(e), n = Je(e, t);
    if (n !== null) {
      var r = fe();
      Fe(n, e, t, r);
    }
    Uo(e, t);
  }
};
Gs = function() {
  return F;
};
Zs = function(e, t) {
  var n = F;
  try {
    return F = e, t();
  } finally {
    F = n;
  }
};
pi = function(e, t, n) {
  switch (t) {
    case "input":
      if (oi(e, n), t = n.name, n.type === "radio" && t != null) {
        for (n = e; n.parentNode; ) n = n.parentNode;
        for (n = n.querySelectorAll("input[name=" + JSON.stringify("" + t) + '][type="radio"]'), t = 0; t < n.length; t++) {
          var r = n[t];
          if (r !== e && r.form === e.form) {
            var l = dl(r);
            if (!l) throw Error(S(90));
            Ps(r), oi(r, l);
          }
        }
      }
      break;
    case "textarea":
      Ls(e, n);
      break;
    case "select":
      t = n.value, t != null && Gt(e, !!n.multiple, t, !1);
  }
};
Os = Mo;
Fs = Mt;
var qd = { usingClientEntryPoint: !1, Events: [nr, Bt, dl, Is, Ds, Mo] }, xn = { findFiberByHostInstance: Ct, bundleType: 0, version: "18.3.1", rendererPackageName: "react-dom" }, bd = { bundleType: xn.bundleType, version: xn.version, rendererPackageName: xn.rendererPackageName, rendererConfig: xn.rendererConfig, overrideHookState: null, overrideHookStateDeletePath: null, overrideHookStateRenamePath: null, overrideProps: null, overridePropsDeletePath: null, overridePropsRenamePath: null, setErrorHandler: null, setSuspenseHandler: null, scheduleUpdate: null, currentDispatcherRef: be.ReactCurrentDispatcher, findHostInstanceByFiber: function(e) {
  return e = Vs(e), e === null ? null : e.stateNode;
}, findFiberByHostInstance: xn.findFiberByHostInstance || Zd, findHostInstancesForRefresh: null, scheduleRefresh: null, scheduleRoot: null, setRefreshHandler: null, getCurrentFiber: null, reconcilerVersion: "18.3.1-next-f1338f8080-20240426" };
if (typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ < "u") {
  var Er = __REACT_DEVTOOLS_GLOBAL_HOOK__;
  if (!Er.isDisabled && Er.supportsFiber) try {
    sl = Er.inject(bd), Be = Er;
  } catch {
  }
}
Ee.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED = qd;
Ee.createPortal = function(e, t) {
  var n = 2 < arguments.length && arguments[2] !== void 0 ? arguments[2] : null;
  if (!Vo(t)) throw Error(S(200));
  return Gd(e, t, null, n);
};
Ee.createRoot = function(e, t) {
  if (!Vo(e)) throw Error(S(299));
  var n = !1, r = "", l = yc;
  return t != null && (t.unstable_strictMode === !0 && (n = !0), t.identifierPrefix !== void 0 && (r = t.identifierPrefix), t.onRecoverableError !== void 0 && (l = t.onRecoverableError)), t = Fo(e, 1, !1, null, null, n, !1, r, l), e[Ze] = t.current, Qn(e.nodeType === 8 ? e.parentNode : e), new Ao(t);
};
Ee.findDOMNode = function(e) {
  if (e == null) return null;
  if (e.nodeType === 1) return e;
  var t = e._reactInternals;
  if (t === void 0)
    throw typeof e.render == "function" ? Error(S(188)) : (e = Object.keys(e).join(","), Error(S(268, e)));
  return e = Vs(t), e = e === null ? null : e.stateNode, e;
};
Ee.flushSync = function(e) {
  return Mt(e);
};
Ee.hydrate = function(e, t, n) {
  if (!Sl(t)) throw Error(S(200));
  return xl(null, e, t, !0, n);
};
Ee.hydrateRoot = function(e, t, n) {
  if (!Vo(e)) throw Error(S(405));
  var r = n != null && n.hydratedSources || null, l = !1, i = "", o = yc;
  if (n != null && (n.unstable_strictMode === !0 && (l = !0), n.identifierPrefix !== void 0 && (i = n.identifierPrefix), n.onRecoverableError !== void 0 && (o = n.onRecoverableError)), t = gc(t, null, e, 1, n ?? null, l, !1, i, o), e[Ze] = t.current, Qn(e), r) for (e = 0; e < r.length; e++) n = r[e], l = n._getVersion, l = l(n._source), t.mutableSourceEagerHydrationData == null ? t.mutableSourceEagerHydrationData = [n, l] : t.mutableSourceEagerHydrationData.push(
    n,
    l
  );
  return new kl(t);
};
Ee.render = function(e, t, n) {
  if (!Sl(t)) throw Error(S(200));
  return xl(null, e, t, !1, n);
};
Ee.unmountComponentAtNode = function(e) {
  if (!Sl(e)) throw Error(S(40));
  return e._reactRootContainer ? (Mt(function() {
    xl(null, null, e, !1, function() {
      e._reactRootContainer = null, e[Ze] = null;
    });
  }), !0) : !1;
};
Ee.unstable_batchedUpdates = Mo;
Ee.unstable_renderSubtreeIntoContainer = function(e, t, n, r) {
  if (!Sl(n)) throw Error(S(200));
  if (e == null || e._reactInternals === void 0) throw Error(S(38));
  return xl(e, t, n, !1, r);
};
Ee.version = "18.3.1-next-f1338f8080-20240426";
function wc() {
  if (!(typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ > "u" || typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE != "function"))
    try {
      __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE(wc);
    } catch (e) {
      console.error(e);
    }
}
wc(), ws.exports = Ee;
var kc = ws.exports, Sc, Ju = kc;
Sc = Ju.createRoot, Ju.hydrateRoot;
function Jl(...e) {
  return e.filter(Boolean).join(" ");
}
function ep({
  content: e,
  children: t,
  className: n,
  triggerClassName: r,
  bubbleClassName: l,
  focusable: i = !1,
  ariaLabel: o,
  inline: u = !0
}) {
  const s = D.useRef(null), c = D.useRef(null), h = D.useRef(null), [v, f] = D.useState(!1), [w, k] = D.useState({}), [E, M] = D.useState("bottom"), d = D.useCallback(() => {
    const g = c.current, C = h.current;
    if (!g || !C || typeof window > "u")
      return;
    const _ = 12, x = 12, N = g.getBoundingClientRect(), I = C.getBoundingClientRect(), y = Math.ceil(I.height), P = Math.ceil(I.width), A = N.left + N.width / 2 - P / 2, ae = Math.min(
      Math.max(_, A),
      Math.max(_, window.innerWidth - P - _)
    ), lr = N.bottom + x + y + _ <= window.innerHeight || N.top < y + x + _ ? "bottom" : "top", pn = lr === "bottom" ? N.bottom + x : Math.max(_, N.top - y - x), mn = N.left + N.width / 2, j = Math.min(
      Math.max(18, mn - ae),
      Math.max(18, P - 18)
    );
    M(lr), k({
      left: ae,
      top: pn,
      "--admin-tooltip-arrow-left": `${j}px`
    });
  }, []);
  D.useLayoutEffect(() => {
    if (!v)
      return;
    d();
    const g = () => d();
    return window.addEventListener("resize", g), window.addEventListener("scroll", g, !0), () => {
      window.removeEventListener("resize", g), window.removeEventListener("scroll", g, !0);
    };
  }, [d, v]);
  const a = u ? "span" : "div", p = u ? "span" : "div";
  return /* @__PURE__ */ m.jsxs(
    a,
    {
      className: Jl("admin-tooltip", n),
      onMouseEnter: () => f(!0),
      onMouseLeave: () => f(!1),
      onFocusCapture: () => f(!0),
      onBlurCapture: (g) => {
        var C;
        (C = s.current) != null && C.contains(g.relatedTarget) || f(!1);
      },
      ref: (g) => {
        s.current = g;
      },
      children: [
        /* @__PURE__ */ m.jsx(
          p,
          {
            className: Jl("admin-tooltip-trigger", r),
            ...i ? { tabIndex: 0, "aria-label": o } : {},
            ref: (g) => {
              c.current = g;
            },
            children: t
          }
        ),
        typeof document < "u" ? kc.createPortal(
          /* @__PURE__ */ m.jsx(
            "span",
            {
              ref: h,
              className: Jl("admin-tooltip-bubble", `placement-${E}`, l, v && "visible"),
              role: "tooltip",
              style: w,
              children: e
            }
          ),
          document.body
        ) : null
      ]
    }
  );
}
function tp(e) {
  return typeof e == "object" && e !== null && !Array.isArray(e);
}
function np(e) {
  var n;
  const t = typeof ((n = globalThis.crypto) == null ? void 0 : n.randomUUID) == "function" ? globalThis.crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${e}-${t}`;
}
function $o(e) {
  const t = /* @__PURE__ */ new Set(), n = [];
  for (const r of e) {
    const l = r.trim();
    !l || t.has(l) || (t.add(l), n.push(l));
  }
  return n;
}
function qu(e) {
  return $o(e).join(`
`);
}
function xc(e = "", t = "") {
  return {
    id: np("map-row"),
    key: e,
    value: t
  };
}
function bu(e) {
  return tp(e) ? Object.entries(e).map(([t, n]) => xc(t, String(n ?? ""))) : [];
}
function es(e, t, n = !1) {
  const r = {}, l = /* @__PURE__ */ new Set();
  for (const i of e) {
    const o = i.key.trim(), u = i.value;
    if (!(!o && !u.trim())) {
      if (!o)
        throw new Error(`${t} contains a value without a key.`);
      if (l.has(o))
        throw new Error(`${t} contains duplicate key '${o}'.`);
      l.add(o), r[o] = u;
    }
  }
  if (!(Object.keys(r).length === 0 && !n))
    return r;
}
function ql(e = "") {
  return {
    id: `string-row-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    value: e
  };
}
function ts(e, t) {
  const n = e.map((r) => t ? t(r) : r);
  return $o(n);
}
function Ec(e, t) {
  return /* @__PURE__ */ m.jsx("div", { className: "empty-state", children: e ?? t });
}
function Cc(e, t, n) {
  if (e.current === n) return !1;
  const r = t.current === n;
  return e.current = n, r ? (t.current = null, !1) : (t.current = null, !0);
}
function ns({
  values: e,
  onChange: t,
  disabled: n = !1,
  className: r,
  description: l,
  emptyState: i,
  addLabel: o = "Add item",
  itemLabel: u = "Value",
  placeholder: s = "Enter a value",
  suggestions: c,
  validateItem: h,
  normalizeItem: v
}) {
  const f = D.useId(), w = ts(e, v), k = qu(w), [E, M] = D.useState(() => w.map((x) => ql(x))), d = D.useRef(k), a = D.useRef(null);
  D.useEffect(() => {
    Cc(d, a, k) && M(w.map((x) => ql(x)));
  }, [w, k]);
  function p(x) {
    const N = ts(
      x.map((I) => I.value),
      v
    );
    a.current = qu(N), t(N);
  }
  const C = (c ?? []).map((x) => String(x)), _ = C.length > 0 ? $o(C) : [];
  return /* @__PURE__ */ m.jsxs("div", { className: ["string-list-selector", "structured-input-editor", r].filter(Boolean).join(" "), children: [
    l ? /* @__PURE__ */ m.jsx("div", { className: "structured-input-description", children: l }) : null,
    /* @__PURE__ */ m.jsx("div", { className: "inline-actions compact-actions", children: /* @__PURE__ */ m.jsx(
      "button",
      {
        type: "button",
        onClick: () => {
          const x = [...E, ql()];
          M(x);
        },
        disabled: n,
        children: o
      }
    ) }),
    E.length === 0 ? Ec(i, `No ${u.toLowerCase()}s are configured.`) : null,
    E.map((x, N) => {
      const I = (h == null ? void 0 : h(x.value, N, E.map((y) => y.value))) ?? null;
      return /* @__PURE__ */ m.jsxs("div", { className: "string-list-selector-row structured-input-row", children: [
        /* @__PURE__ */ m.jsxs("label", { className: "field-stack structured-input-field", children: [
          /* @__PURE__ */ m.jsx("span", { children: u }),
          /* @__PURE__ */ m.jsx(
            "input",
            {
              list: _.length > 0 ? f : void 0,
              value: x.value,
              onChange: (y) => {
                const P = v ? v(y.target.value) : y.target.value, A = E.map(
                  (ae, dn) => dn === N ? { ...ae, value: P } : ae
                );
                M(A), p(A);
              },
              placeholder: s,
              disabled: n
            }
          ),
          I ? /* @__PURE__ */ m.jsx("div", { className: "structured-input-error", children: I }) : null
        ] }),
        /* @__PURE__ */ m.jsx("div", { className: "inline-actions compact-actions", children: /* @__PURE__ */ m.jsx(
          "button",
          {
            type: "button",
            className: "danger",
            onClick: () => {
              const y = E.filter((P, A) => A !== N);
              M(y), p(y);
            },
            disabled: n,
            children: "Remove"
          }
        ) })
      ] }, x.id);
    }),
    _.length > 0 ? /* @__PURE__ */ m.jsx("datalist", { id: f, children: _.map((x) => /* @__PURE__ */ m.jsx("option", { value: x }, x)) }) : null
  ] });
}
function rp({
  value: e,
  onChange: t,
  onValidationChange: n,
  fieldLabel: r,
  disabled: l = !1,
  className: i,
  description: o,
  emptyState: u,
  addLabel: s = "Add row",
  keyLabel: c = "Key",
  valueLabel: h = "Value"
}) {
  const v = JSON.stringify(e ?? {}), [f, w] = D.useState(() => bu(e)), [k, E] = D.useState(null), M = D.useRef(v), d = D.useRef(null);
  D.useEffect(() => {
    Cc(M, d, v) && (w(bu(e)), E(null));
  }, [v, e]);
  function a(g) {
    try {
      const _ = es(g, r, !1) ?? {};
      d.current = JSON.stringify(_), t(_), E(null);
    } catch (C) {
      E(C instanceof Error ? C.message : String(C));
    }
  }
  const p = k ?? (() => {
    try {
      return es(f, r, !1), null;
    } catch (g) {
      return g instanceof Error ? g.message : String(g);
    }
  })();
  return D.useEffect(() => {
    n == null || n(p);
  }, [n, p]), /* @__PURE__ */ m.jsxs("div", { className: ["structured-input-editor", i].filter(Boolean).join(" "), children: [
    o ? /* @__PURE__ */ m.jsx("div", { className: "structured-input-description", children: o }) : null,
    /* @__PURE__ */ m.jsx("div", { className: "inline-actions compact-actions", children: /* @__PURE__ */ m.jsx(
      "button",
      {
        type: "button",
        onClick: () => w((g) => [...g, xc()]),
        disabled: l,
        children: s
      }
    ) }),
    k ? /* @__PURE__ */ m.jsx("div", { className: "callout error-callout", children: k }) : null,
    f.length === 0 ? Ec(u, `No ${r.toLowerCase()} rows are configured.`) : null,
    f.map((g, C) => /* @__PURE__ */ m.jsxs("div", { className: "string-list-selector-row structured-input-row structured-input-map-row", children: [
      /* @__PURE__ */ m.jsxs("label", { className: "field-stack structured-input-field", children: [
        /* @__PURE__ */ m.jsx("span", { children: c }),
        /* @__PURE__ */ m.jsx(
          "input",
          {
            value: g.key,
            onChange: (_) => {
              const x = f.map(
                (N, I) => I === C ? { ...N, key: _.target.value } : N
              );
              w(x), a(x);
            },
            disabled: l
          }
        )
      ] }),
      /* @__PURE__ */ m.jsxs("label", { className: "field-stack structured-input-field", children: [
        /* @__PURE__ */ m.jsx("span", { children: h }),
        /* @__PURE__ */ m.jsx(
          "input",
          {
            value: g.value,
            onChange: (_) => {
              const x = f.map(
                (N, I) => I === C ? { ...N, value: _.target.value } : N
              );
              w(x), a(x);
            },
            disabled: l
          }
        )
      ] }),
      /* @__PURE__ */ m.jsx("div", { className: "inline-actions compact-actions", children: /* @__PURE__ */ m.jsx(
        "button",
        {
          type: "button",
          className: "danger",
          onClick: () => {
            const _ = f.filter((x, N) => N !== C);
            w(_), a(_);
          },
          disabled: l,
          children: "Remove"
        }
      ) })
    ] }, g.id))
  ] });
}
const lp = "";
class ip extends Error {
  constructor(n, r, l) {
    super(n);
    Cl(this, "status");
    Cl(this, "payload");
    this.name = "ApiError", this.status = r, this.payload = l;
  }
}
async function Bo(e, t) {
  const n = await fetch(`${lp}${e}`, {
    ...t,
    headers: {
      "Content-Type": "application/json",
      ...(t == null ? void 0 : t.headers) ?? {}
    }
  }), r = n.headers.get("content-type") ?? "";
  let l;
  if (r.includes("application/json"))
    l = await n.json();
  else {
    const i = await n.text();
    throw new Error(i.trim() || `${n.status} ${n.statusText}`);
  }
  if (!n.ok && typeof l == "object" && l && "error" in l)
    throw new ip(String(l.error), n.status, l);
  return l;
}
function rs() {
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
    vmImage: "",
    containerImage: "",
    dotnet: !1,
    nuget: !1,
    mssql: !1,
    mssqlInitRoot: "",
    playwright: !1,
    initialPolicy: op()
  };
}
function op() {
  return {
    labels: {},
    webSearchEnabled: !1,
    allowedTools: [],
    allowedScriptNames: [],
    communicationTargetIds: []
  };
}
function Or(e) {
  const t = /* @__PURE__ */ new Set(), n = [];
  for (const r of e) {
    const l = r.trim();
    !l || t.has(l) || (t.add(l), n.push(l));
  }
  return n;
}
function _e(e) {
  return e.trim() || null;
}
function _c(e) {
  const t = e.trim().toLowerCase().match(
    /^(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks)$/
  );
  if (!t) return null;
  const n = Number(t[1]);
  if (!Number.isInteger(n) || n <= 0) return null;
  const r = {
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
    h: 60 * 60,
    hr: 60 * 60,
    hrs: 60 * 60,
    hour: 60 * 60,
    hours: 60 * 60,
    d: 24 * 60 * 60,
    day: 24 * 60 * 60,
    days: 24 * 60 * 60,
    w: 7 * 24 * 60 * 60,
    week: 7 * 24 * 60 * 60,
    weeks: 7 * 24 * 60 * 60
  };
  return n * r[t[2]];
}
function up(e, t) {
  const n = {
    backend: e.backend,
    session_id: _e(e.sessionId),
    directory_path: _e(t)
  }, r = _c(e.ttlDuration);
  r != null && (n.ttl_seconds = r);
  const l = _e(e.model);
  l && (n.model = l);
  const i = _e(e.agentProvider);
  i && (n.agent_provider = i);
  const o = _e(e.modelProviderMode);
  if (o && (n.model_provider_mode = o), n.dotnet = e.dotnet, n.nuget = e.nuget, n.playwright = e.playwright, e.backend === "libvirt_vm") {
    const v = _e(e.provider);
    n.vm_name = _e(e.vmName), v && (n.provider = v), n.vm_user = _e(e.vmUser) ?? "agent";
    const f = _e(e.vmImage);
    f && (n.vm_image = f), n.mssql = e.mssql, n.mssql_init_root = e.mssql ? _e(e.mssqlInitRoot) : null;
  } else
    n.target_name = _e(e.targetName), n.container_image = _e(e.containerImage);
  const u = e.initialPolicy;
  Object.keys(u.labels).length > 0 && (n.labels = u.labels), u.webSearchEnabled && (n.web_search_enabled = !0);
  const s = Or(u.allowedTools);
  s.length > 0 && (n.allowed_tools = s);
  const c = Or(u.allowedScriptNames);
  c.length > 0 && (n.allowed_script_names = c);
  const h = Or(u.communicationTargetIds);
  return h.length > 0 && (n.communication_target_ids = h), n;
}
function sp(e) {
  return e.vmName.trim() || e.targetName.trim() || e.sessionId.trim() || "agent";
}
async function ap(e) {
  const t = new URLSearchParams(), n = e.trim();
  n && t.set("directory_path", n);
  const r = t.toString();
  return Bo(
    `/api/admin/agents/create-options${r ? `?${r}` : ""}`
  );
}
async function cp(e) {
  return (await Bo(
    "/api/admin/agents/create-operations",
    {
      method: "POST",
      body: JSON.stringify(e)
    }
  )).operation;
}
async function fp(e) {
  return (await Bo(
    `/api/admin/agents/create-operations/${encodeURIComponent(e)}`
  )).operation;
}
function dp(e) {
  return e.status === "queued" || e.status === "running";
}
function pp(e) {
  var r;
  const t = (r = e.session_id) == null ? void 0 : r.trim();
  if (t) return t;
  const n = e.result;
  if (!n) return null;
  for (const l of ["session_id", "sessionId"]) {
    const i = n[l];
    if (typeof i == "string" && i.trim()) return i.trim();
  }
  for (const l of ["session", "agent"]) {
    const i = n[l];
    if (typeof i != "object" || i === null || Array.isArray(i)) continue;
    const o = i.session_id;
    if (typeof o == "string" && o.trim()) return o.trim();
  }
  return null;
}
function B({ children: e, tooltip: t }) {
  const n = typeof e == "string" ? e : "Field";
  return /* @__PURE__ */ m.jsxs("span", { className: "config-field-label", children: [
    /* @__PURE__ */ m.jsx("span", { children: e }),
    /* @__PURE__ */ m.jsx(
      ep,
      {
        content: t,
        focusable: !0,
        ariaLabel: `${n} help`,
        triggerClassName: "config-field-tooltip-trigger",
        children: /* @__PURE__ */ m.jsx("span", { className: "config-field-tooltip-icon", "aria-hidden": "true", children: "i" })
      }
    )
  ] });
}
function ls(e, t) {
  var r;
  const n = (r = e.metadata) == null ? void 0 : r[t];
  return typeof n == "string" ? n.trim() : "";
}
function mp(e, t) {
  var r;
  const n = (r = e.metadata) == null ? void 0 : r[t];
  return Array.isArray(n) ? n.map((l) => typeof l == "string" ? l.trim() : "").filter(Boolean) : [];
}
function is(e, t = "Unknown") {
  const n = e.trim();
  return n ? n.replace(/[_-]+/g, " ").replace(/\s+/g, " ").replace(/\b\w/g, (r) => r.toUpperCase()) : t;
}
function hp(e) {
  var l;
  const t = (l = e.description) == null ? void 0 : l.trim();
  if (t) return t;
  const n = e.label.trim(), r = e.value.trim();
  return n && n !== r && !n.startsWith(`${r} (`) ? n : null;
}
function vp({
  values: e,
  options: t,
  onChange: n,
  emptyState: r
}) {
  const l = Or([...e]), i = new Set(l), o = new Set(t.map((h) => h.value)), u = l.filter((h) => !o.has(h)), s = [
    ...t.map((h) => ({
      value: h.value,
      detail: hp(h),
      provider: is(ls(h, "provider")),
      shape: is(ls(h, "target_shape")),
      capabilities: mp(h, "policy_capabilities"),
      unknown: !1
    })),
    ...u.map((h) => ({
      value: h,
      detail: "Selected target is no longer in the directory options.",
      provider: "Unknown",
      shape: "Unknown",
      capabilities: [],
      unknown: !0
    }))
  ];
  function c(h, v) {
    const f = v ? [...l.filter((w) => w !== h), h] : l.filter((w) => w !== h);
    n(f);
  }
  return /* @__PURE__ */ m.jsxs("div", { className: "agent-communication-target-selector", children: [
    s.length > 0 ? /* @__PURE__ */ m.jsx("div", { className: "agent-communication-target-table-wrap", children: /* @__PURE__ */ m.jsxs("table", { className: "agent-communication-target-table", children: [
      /* @__PURE__ */ m.jsx("thead", { children: /* @__PURE__ */ m.jsxs("tr", { children: [
        /* @__PURE__ */ m.jsx("th", { scope: "col", children: "Select" }),
        /* @__PURE__ */ m.jsx("th", { scope: "col", children: "Target" }),
        /* @__PURE__ */ m.jsx("th", { scope: "col", children: "Provider" }),
        /* @__PURE__ */ m.jsx("th", { scope: "col", children: "Shape" }),
        /* @__PURE__ */ m.jsx("th", { scope: "col", children: "Capabilities" })
      ] }) }),
      /* @__PURE__ */ m.jsx("tbody", { children: s.map((h) => {
        const v = i.has(h.value);
        return /* @__PURE__ */ m.jsxs("tr", { className: v ? "selected" : "", children: [
          /* @__PURE__ */ m.jsx("td", { className: "agent-communication-target-select-cell", children: /* @__PURE__ */ m.jsx(
            "input",
            {
              type: "checkbox",
              checked: v,
              onChange: (f) => c(h.value, f.target.checked),
              "aria-label": `Allow communication target ${h.value}`
            }
          ) }),
          /* @__PURE__ */ m.jsx("td", { children: /* @__PURE__ */ m.jsxs("div", { className: "agent-communication-target-name", children: [
            /* @__PURE__ */ m.jsx("strong", { children: h.value }),
            h.detail ? /* @__PURE__ */ m.jsx("span", { children: h.detail }) : null,
            h.unknown ? /* @__PURE__ */ m.jsx("span", { className: "agent-communication-target-warning", children: "Unknown target" }) : null
          ] }) }),
          /* @__PURE__ */ m.jsx("td", { children: h.provider }),
          /* @__PURE__ */ m.jsx("td", { children: h.shape }),
          /* @__PURE__ */ m.jsx("td", { children: h.capabilities.length > 0 ? /* @__PURE__ */ m.jsx("span", { className: "agent-communication-target-capabilities", children: h.capabilities.map((f) => /* @__PURE__ */ m.jsx("span", { children: f }, f)) }) : /* @__PURE__ */ m.jsx("span", { className: "muted-text", children: "None" }) })
        ] }, h.value);
      }) })
    ] }) }) : null,
    l.length === 0 ? /* @__PURE__ */ m.jsx("div", { className: "empty-state", children: r }) : null
  ] });
}
function gp(e, t, n) {
  const r = ((n == null ? void 0 : n.agent_providers) ?? []).some(
    (i) => i.value === e.agentProvider && i.execution_backends.includes(t)
  ), l = e.agentProvider && r ? e.agentProvider : "";
  return {
    ...e,
    backend: t,
    mssql: t === "libvirt_vm" ? e.mssql : !1,
    mssqlInitRoot: t === "libvirt_vm" ? e.mssqlInitRoot : "",
    agentProvider: l,
    modelProviderMode: l && l !== "codex" ? "" : e.modelProviderMode
  };
}
function yp(e) {
  const t = e.trim();
  return !t || _c(t) == null;
}
function wp({
  directoryPath: e,
  onClose: t,
  onCreate: n,
  title: r = "Create agent",
  description: l = null,
  submitLabel: i = "Create",
  submittingLabel: o = "Queueing...",
  closeLabel: u = "Close",
  privilegedUnlocked: s = !0,
  closeOnSuccess: c = !0,
  requireSessionId: h = !1,
  requireSessionIdMessage: v = "Session id is required."
}) {
  const [f, w] = D.useState(() => rs()), [k, E] = D.useState(null), [M, d] = D.useState(""), [a, p] = D.useState(null), [g, C] = D.useState(!1);
  D.useEffect(() => {
    let y = !1;
    return E(null), w((A) => ({
      ...A,
      agentProvider: "",
      vmImage: "",
      containerImage: "",
      initialPolicy: {
        ...A.initialPolicy,
        communicationTargetIds: []
      }
    })), (async () => {
      try {
        const A = await ap(e);
        y || E(A);
      } catch {
        y || E(null);
      }
    })(), () => {
      y = !0;
    };
  }, [e]);
  const _ = D.useMemo(
    () => ((k == null ? void 0 : k.agent_providers) ?? []).filter(
      (y) => y.execution_backends.includes(f.backend)
    ),
    [f.backend, k]
  ), x = !f.agentProvider || f.agentProvider === "codex", N = (k == null ? void 0 : k.communication_targets) ?? [];
  async function I() {
    if (f.backend === "libvirt_vm" && !f.vmName.trim()) {
      d("VM name is required.");
      return;
    }
    if (h && !f.sessionId.trim()) {
      d(v);
      return;
    }
    if (f.backend === "container_worker_vm" && !f.sessionId.trim()) {
      d("Session id is required for container workers.");
      return;
    }
    if (yp(f.ttlDuration)) {
      d(
        "Session TTL must use seconds, minutes, hours, days, or weeks, for example 30m, 8h, 7d, or 2w."
      );
      return;
    }
    if (a) {
      d(a);
      return;
    }
    const y = f, P = up(y, e), A = sp(y);
    C(!0), d("");
    try {
      await n(P, { form: y, targetLabel: A }), w(rs()), p(null), c && t();
    } catch (ae) {
      d(ae instanceof Error ? ae.message : String(ae));
    } finally {
      C(!1);
    }
  }
  return /* @__PURE__ */ m.jsx("div", { className: "modal-backdrop", role: "presentation", children: /* @__PURE__ */ m.jsxs("section", { className: "modal-card agent-crud-modal", role: "dialog", "aria-modal": "true", "aria-label": r, children: [
    /* @__PURE__ */ m.jsxs("div", { className: "modal-header", children: [
      /* @__PURE__ */ m.jsxs("div", { children: [
        /* @__PURE__ */ m.jsx("h2", { children: r }),
        l ? /* @__PURE__ */ m.jsx("p", { children: l }) : null
      ] }),
      /* @__PURE__ */ m.jsx("button", { className: "admin-action-button compact", type: "button", onClick: t, disabled: g, children: u })
    ] }),
    /* @__PURE__ */ m.jsxs("div", { className: "modal-body", children: [
      M ? /* @__PURE__ */ m.jsx("div", { className: "callout error-callout", children: M }) : null,
      /* @__PURE__ */ m.jsxs("div", { className: "config-field-grid", children: [
        /* @__PURE__ */ m.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ m.jsx(B, { tooltip: "Execution target for the agent. VM agent launches a libvirt VM; Container worker prepares a worker session that runs in a managed container.", children: "Backend" }),
          /* @__PURE__ */ m.jsx("span", { className: "select-field-control", children: /* @__PURE__ */ m.jsxs(
            "select",
            {
              value: f.backend,
              onChange: (y) => {
                const P = y.target.value;
                w((A) => gp(A, P, k));
              },
              children: [
                /* @__PURE__ */ m.jsx("option", { value: "libvirt_vm", children: "VM agent" }),
                /* @__PURE__ */ m.jsx("option", { value: "container_worker_vm", children: "Container worker" })
              ]
            }
          ) })
        ] }),
        /* @__PURE__ */ m.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ m.jsx(B, { tooltip: "Agent CLI to bootstrap and run in the target. Claude bootstraps Claude Code for VM agents; Directory default uses the configured provider default.", children: "Agent provider" }),
          /* @__PURE__ */ m.jsx("span", { className: "select-field-control", children: /* @__PURE__ */ m.jsxs(
            "select",
            {
              value: f.agentProvider,
              onChange: (y) => w((P) => {
                const A = y.target.value;
                return {
                  ...P,
                  agentProvider: A,
                  modelProviderMode: A && A !== "codex" ? "" : P.modelProviderMode
                };
              }),
              children: [
                /* @__PURE__ */ m.jsx("option", { value: "", children: "Directory default" }),
                _.map((y) => /* @__PURE__ */ m.jsx("option", { value: y.value, children: y.label }, y.value))
              ]
            }
          ) })
        ] }),
        x ? /* @__PURE__ */ m.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ m.jsx(B, { tooltip: "Relay mode for Codex model requests. Leave blank to use the directory default; choose Local OpenAI for host-local Ollama.", children: "Model relay" }),
          /* @__PURE__ */ m.jsx("span", { className: "select-field-control", children: /* @__PURE__ */ m.jsxs(
            "select",
            {
              value: f.modelProviderMode,
              onChange: (y) => w((P) => ({
                ...P,
                modelProviderMode: y.target.value
              })),
              children: [
                /* @__PURE__ */ m.jsx("option", { value: "", children: "Directory default" }),
                ((k == null ? void 0 : k.model_provider_modes) ?? []).map((y) => /* @__PURE__ */ m.jsx("option", { value: y.value, children: y.label }, y.value))
              ]
            }
          ) })
        ] }) : null,
        /* @__PURE__ */ m.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ m.jsx(B, { tooltip: "Host-side session id for policy, logs, model routing, and resume state. Required for container workers; VM agents can derive one from the VM name.", children: "Session" }),
          /* @__PURE__ */ m.jsx(
            "input",
            {
              value: f.sessionId,
              onChange: (y) => w((P) => ({ ...P, sessionId: y.target.value })),
              placeholder: h || f.backend === "container_worker_vm" ? "Required" : "Optional"
            }
          )
        ] }),
        /* @__PURE__ */ m.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ m.jsx(B, { tooltip: "How long the created session remains active before expiry. Use s, m, h, d, or w, for example 30m, 8h, 7d, or 2w.", children: "Session TTL" }),
          /* @__PURE__ */ m.jsx(
            "input",
            {
              value: f.ttlDuration,
              onChange: (y) => w((P) => ({ ...P, ttlDuration: y.target.value })),
              placeholder: "7d"
            }
          )
        ] }),
        /* @__PURE__ */ m.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ m.jsx(B, { tooltip: "Model stored on the session for runtime prompts and resumes. Leave blank to use the selected agent provider or directory default.", children: "Model" }),
          /* @__PURE__ */ m.jsx(
            "input",
            {
              value: f.model,
              onChange: (y) => w((P) => ({ ...P, model: y.target.value })),
              placeholder: f.agentProvider === "claude" ? "claude-sonnet-4-6" : "Directory default"
            }
          )
        ] }),
        f.backend === "libvirt_vm" ? /* @__PURE__ */ m.jsxs(m.Fragment, { children: [
          /* @__PURE__ */ m.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ m.jsx(B, { tooltip: "Libvirt domain name to create for this VM-backed agent. It must be unique on the host.", children: "VM name" }),
            /* @__PURE__ */ m.jsx(
              "input",
              {
                value: f.vmName,
                onChange: (y) => w((P) => ({ ...P, vmName: y.target.value }))
              }
            )
          ] }),
          /* @__PURE__ */ m.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ m.jsx(B, { tooltip: "VM infrastructure provider, not the model provider. Leave blank to use the directory default, usually libvirt.", children: "Provider" }),
            /* @__PURE__ */ m.jsx(
              "input",
              {
                value: f.provider,
                onChange: (y) => w((P) => ({ ...P, provider: y.target.value })),
                placeholder: "Directory default"
              }
            )
          ] }),
          /* @__PURE__ */ m.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ m.jsx(B, { tooltip: "Guest runtime user used for bootstraps and agent commands. Current standard VM images use agent.", children: "VM user" }),
            /* @__PURE__ */ m.jsx(
              "input",
              {
                value: f.vmUser,
                onChange: (y) => w((P) => ({ ...P, vmUser: y.target.value }))
              }
            )
          ] }),
          /* @__PURE__ */ m.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ m.jsx(B, { tooltip: "Approved libvirt template alias for the base qcow2 image. Leave blank to use the directory default.", children: "VM image" }),
            /* @__PURE__ */ m.jsx("span", { className: "select-field-control", children: /* @__PURE__ */ m.jsxs(
              "select",
              {
                value: f.vmImage,
                onChange: (y) => w((P) => ({ ...P, vmImage: y.target.value })),
                children: [
                  /* @__PURE__ */ m.jsx("option", { value: "", children: `Directory default (${(k == null ? void 0 : k.libvirt.default_template) ?? "default"})` }),
                  ((k == null ? void 0 : k.libvirt.templates) ?? []).map((y) => /* @__PURE__ */ m.jsx("option", { value: y.alias, children: y.alias }, y.alias))
                ]
              }
            ) })
          ] })
        ] }) : /* @__PURE__ */ m.jsxs(m.Fragment, { children: [
          /* @__PURE__ */ m.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ m.jsx(B, { tooltip: "Display and control-plane target name for this container-worker agent. Leave blank to reuse the session id.", children: "Target name" }),
            /* @__PURE__ */ m.jsx(
              "input",
              {
                value: f.targetName,
                onChange: (y) => w((P) => ({ ...P, targetName: y.target.value })),
                placeholder: "Optional"
              }
            )
          ] }),
          /* @__PURE__ */ m.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ m.jsx(B, { tooltip: "Approved container image alias for worker sessions. Leave blank to use the directory default runner image.", children: "Container image selector" }),
            k ? /* @__PURE__ */ m.jsx("span", { className: "select-field-control", children: /* @__PURE__ */ m.jsxs(
              "select",
              {
                value: f.containerImage,
                onChange: (y) => w((P) => ({ ...P, containerImage: y.target.value })),
                children: [
                  /* @__PURE__ */ m.jsx("option", { value: "", children: `Default image (${k.container_worker.default_image ?? "default"})` }),
                  k.container_worker.images.map((y) => /* @__PURE__ */ m.jsx("option", { value: y.alias, children: y.alias }, y.alias))
                ]
              }
            ) }) : /* @__PURE__ */ m.jsx(
              "input",
              {
                value: f.containerImage,
                onChange: (y) => w((P) => ({ ...P, containerImage: y.target.value })),
                placeholder: "Alias or default image"
              }
            )
          ] })
        ] }),
        /* @__PURE__ */ m.jsxs("fieldset", { className: "agent-capability-group", children: [
          /* @__PURE__ */ m.jsx("legend", { children: /* @__PURE__ */ m.jsx(B, { tooltip: "Optional capabilities to enable when the agent is created.", children: "Capabilities" }) }),
          /* @__PURE__ */ m.jsxs("div", { className: "agent-capability-grid", children: [
            /* @__PURE__ */ m.jsxs("label", { className: "checkbox-row agent-capability-option", children: [
              /* @__PURE__ */ m.jsx(
                "input",
                {
                  type: "checkbox",
                  checked: f.dotnet,
                  onChange: (y) => w((P) => ({ ...P, dotnet: y.target.checked }))
                }
              ),
              /* @__PURE__ */ m.jsx(B, { tooltip: "Seed .NET support for the session. VM agents copy the host SDK; container workers point at the seeded worker cache mount.", children: ".NET" })
            ] }),
            /* @__PURE__ */ m.jsxs("label", { className: "checkbox-row agent-capability-option", children: [
              /* @__PURE__ */ m.jsx(
                "input",
                {
                  type: "checkbox",
                  checked: f.nuget,
                  onChange: (y) => w((P) => ({ ...P, nuget: y.target.checked }))
                }
              ),
              /* @__PURE__ */ m.jsx(B, { tooltip: "Configure package restore through the host NuGet relay and session cache.", children: "NuGet" })
            ] }),
            f.backend === "libvirt_vm" ? /* @__PURE__ */ m.jsxs("label", { className: "checkbox-row agent-capability-option", children: [
              /* @__PURE__ */ m.jsx(
                "input",
                {
                  type: "checkbox",
                  checked: f.mssql,
                  onChange: (y) => w((P) => ({ ...P, mssql: y.target.checked }))
                }
              ),
              /* @__PURE__ */ m.jsx(B, { tooltip: "Provision a guest-local SQL Server instance for VM agents. Container worker SQL uses session-owned MCP provisioning instead.", children: "MSSQL" })
            ] }) : null,
            /* @__PURE__ */ m.jsxs("label", { className: "checkbox-row agent-capability-option", children: [
              /* @__PURE__ */ m.jsx(
                "input",
                {
                  type: "checkbox",
                  checked: f.playwright,
                  onChange: (y) => w((P) => ({ ...P, playwright: y.target.checked }))
                }
              ),
              /* @__PURE__ */ m.jsx(B, { tooltip: "Seed Playwright support for the session. VM agents install browser dependencies; container workers point at the seeded browser cache.", children: "Playwright" })
            ] }),
            /* @__PURE__ */ m.jsxs("label", { className: "checkbox-row agent-capability-option", children: [
              /* @__PURE__ */ m.jsx(
                "input",
                {
                  type: "checkbox",
                  checked: f.initialPolicy.webSearchEnabled,
                  onChange: (y) => w((P) => ({
                    ...P,
                    initialPolicy: { ...P.initialPolicy, webSearchEnabled: y.target.checked }
                  }))
                }
              ),
              /* @__PURE__ */ m.jsx(B, { tooltip: "Allow this session's agent runner to use live web search when the selected provider supports it.", children: "Web search" })
            ] })
          ] }),
          f.backend === "libvirt_vm" && f.mssql ? /* @__PURE__ */ m.jsx("div", { className: "agent-capability-detail-row", children: /* @__PURE__ */ m.jsxs("label", { className: "field-stack agent-capability-mssql-root", children: [
            /* @__PURE__ */ m.jsx(B, { tooltip: "Optional host directory copied into the SQL init folder. If init.sql exists there, it runs after SQL Server starts.", children: "MSSQL init root" }),
            /* @__PURE__ */ m.jsx(
              "input",
              {
                value: f.mssqlInitRoot,
                onChange: (y) => w((P) => ({ ...P, mssqlInitRoot: y.target.value }))
              }
            )
          ] }) }) : null
        ] }),
        /* @__PURE__ */ m.jsxs("div", { className: "field-stack wide-field", children: [
          /* @__PURE__ */ m.jsx(B, { tooltip: "Session policy values written when the agent is created. Empty rows are ignored.", children: "Initial policy" }),
          /* @__PURE__ */ m.jsx("p", { className: "field-description", children: "Optional session policy values to apply when the agent is created. Blank rows are not sent." })
        ] }),
        /* @__PURE__ */ m.jsxs("div", { className: "field-stack", children: [
          /* @__PURE__ */ m.jsx(B, { tooltip: "Key/value metadata stored on the session and shown in agent lists.", children: "Labels" }),
          /* @__PURE__ */ m.jsx(
            rp,
            {
              value: f.initialPolicy.labels,
              onChange: (y) => w((P) => ({
                ...P,
                initialPolicy: { ...P.initialPolicy, labels: y }
              })),
              onValidationChange: p,
              fieldLabel: "Labels",
              emptyState: "No labels are configured.",
              addLabel: "Add label",
              keyLabel: "Key",
              valueLabel: "Value"
            }
          )
        ] }),
        /* @__PURE__ */ m.jsxs("div", { className: "field-stack", children: [
          /* @__PURE__ */ m.jsx(B, { tooltip: "MCP tools this session may call without being hidden by policy. Approval rules still apply.", children: "Allowed tools" }),
          /* @__PURE__ */ m.jsx(
            ns,
            {
              values: f.initialPolicy.allowedTools,
              onChange: (y) => w((P) => ({
                ...P,
                initialPolicy: { ...P.initialPolicy, allowedTools: y }
              })),
              addLabel: "Add tool",
              itemLabel: "Tool",
              placeholder: "run_script",
              emptyState: "No tools are allowed explicitly."
            }
          )
        ] }),
        /* @__PURE__ */ m.jsxs("div", { className: "field-stack", children: [
          /* @__PURE__ */ m.jsx(B, { tooltip: "Script names this session may run through approved script execution paths.", children: "Allowed scripts" }),
          /* @__PURE__ */ m.jsx(
            ns,
            {
              values: f.initialPolicy.allowedScriptNames,
              onChange: (y) => w((P) => ({
                ...P,
                initialPolicy: { ...P.initialPolicy, allowedScriptNames: y }
              })),
              addLabel: "Add script",
              itemLabel: "Script",
              placeholder: "collect-logs",
              emptyState: "No scripts are allowed explicitly."
            }
          )
        ] }),
        /* @__PURE__ */ m.jsxs("div", { className: "field-stack wide-field agent-communication-targets-field", children: [
          /* @__PURE__ */ m.jsx(B, { tooltip: "Directory communication targets the session may use for messaging. Only registered targets with session-compatible capabilities are selectable.", children: "Communication targets" }),
          /* @__PURE__ */ m.jsx(
            vp,
            {
              values: f.initialPolicy.communicationTargetIds,
              options: N,
              onChange: (y) => w((P) => ({
                ...P,
                initialPolicy: { ...P.initialPolicy, communicationTargetIds: y }
              })),
              emptyState: N.length ? "No communication targets selected." : "No session-capable communication targets are registered for this directory."
            }
          )
        ] })
      ] }),
      /* @__PURE__ */ m.jsx("div", { className: "inline-actions compact-actions", children: /* @__PURE__ */ m.jsx("button", { className: "admin-action-button primary compact", type: "button", disabled: !s || g, onClick: () => void I(), children: g ? o : i }) })
    ] })
  ] }) });
}
const kp = 1500, Sp = 10 * 60 * 1e3, os = "Session id is required so the created core session can be registered in Team.";
function xp(e) {
  return new Promise((t) => window.setTimeout(t, e));
}
async function Ep(e) {
  let t = e;
  const n = Date.now() + Sp;
  for (; dp(t); ) {
    if (Date.now() >= n)
      throw new Error(`Timed out waiting for ${t.target_name} to finish creating.`);
    await xp(kp), t = await fp(t.operation_id);
  }
  if (t.status === "failed")
    throw new Error(t.error || `Agent create failed for ${t.target_name}.`);
  return t;
}
function Cp(e) {
  window.dispatchEvent(new CustomEvent("team-agent-create:created", { detail: e }));
}
function _p() {
  return new URLSearchParams(window.location.search).get("privileged_unlocked") === "true";
}
function Np() {
  var u;
  const [e, t] = D.useState(null), [n, r] = D.useState(""), [l, i] = D.useState(_p);
  if (D.useEffect(() => {
    const s = (c) => {
      var v;
      if (c.origin !== window.location.origin)
        return;
      const h = c.data || {};
      h.type === "admin-privileged-operations" && i(!!((v = h.privileged_operations) != null && v.unlocked));
    };
    return window.addEventListener("message", s), () => {
      window.removeEventListener("message", s);
    };
  }, []), D.useEffect(() => {
    const s = (h) => {
      r(""), t({
        contextId: (h == null ? void 0 : h.contextId) ?? null,
        directoryPath: (h == null ? void 0 : h.directoryPath) ?? "root"
      });
    };
    window.openTeamAgentCreateDialog = s;
    const c = (h) => {
      s(h.detail ?? {});
    };
    return window.addEventListener("team-agent-create:open", c), () => {
      window.openTeamAgentCreateDialog === s && delete window.openTeamAgentCreateDialog, window.removeEventListener("team-agent-create:open", c);
    };
  }, []), !e)
    return null;
  async function o(s) {
    var f;
    if (!l)
      throw new Error("Unlock privileged operations to create a core agent session.");
    if (!((f = s.session_id) != null && f.trim()))
      throw new Error(os);
    r("Queueing core agent session.");
    const c = await cp(s);
    r(`Creating ${c.target_name}.`);
    const h = await Ep(c), v = pp(h);
    if (!v)
      throw new Error(`Created ${h.target_name}, but the create operation did not return a session id.`);
    r("Registering Team agent."), Cp({
      contextId: (e == null ? void 0 : e.contextId) ?? null,
      sessionId: v,
      targetName: h.target_name,
      operation: h
    });
  }
  return /* @__PURE__ */ m.jsx(
    wp,
    {
      directoryPath: ((u = e.directoryPath) == null ? void 0 : u.trim()) || "root",
      title: "Create and register agent",
      description: n || "Create a VM or container session, then Team registration will continue automatically.",
      submitLabel: "Create and register",
      submittingLabel: "Creating...",
      privilegedUnlocked: l,
      requireSessionId: !0,
      requireSessionIdMessage: os,
      closeOnSuccess: !0,
      onClose: () => {
        t(null), r("");
      },
      onCreate: o
    }
  );
}
function Pp() {
  let e = document.querySelector("#team-agent-create-root");
  return e || (e = document.createElement("div"), e.id = "team-agent-create-root", document.body.appendChild(e)), e;
}
let bl = null;
function us() {
  const e = Pp();
  bl || (bl = Sc(e)), bl.render(/* @__PURE__ */ m.jsx(Np, {}));
}
document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", us, { once: !0 }) : us();
