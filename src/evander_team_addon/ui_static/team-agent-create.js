var Cc = Object.defineProperty;
var _c = (e, t, n) => t in e ? Cc(e, t, { enumerable: !0, configurable: !0, writable: !0, value: n }) : e[t] = n;
var Cl = (e, t, n) => _c(e, typeof t != "symbol" ? t + "" : t, n);
var us = { exports: {} }, ul = {}, ss = { exports: {} }, M = {};
/**
 * @license React
 * react.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var er = Symbol.for("react.element"), Nc = Symbol.for("react.portal"), Pc = Symbol.for("react.fragment"), jc = Symbol.for("react.strict_mode"), Lc = Symbol.for("react.profiler"), Tc = Symbol.for("react.provider"), zc = Symbol.for("react.context"), Rc = Symbol.for("react.forward_ref"), Mc = Symbol.for("react.suspense"), Ic = Symbol.for("react.memo"), Dc = Symbol.for("react.lazy"), Qi = Symbol.iterator;
function Oc(e) {
  return e === null || typeof e != "object" ? null : (e = Qi && e[Qi] || e["@@iterator"], typeof e == "function" ? e : null);
}
var as = { isMounted: function() {
  return !1;
}, enqueueForceUpdate: function() {
}, enqueueReplaceState: function() {
}, enqueueSetState: function() {
} }, cs = Object.assign, fs = {};
function an(e, t, n) {
  this.props = e, this.context = t, this.refs = fs, this.updater = n || as;
}
an.prototype.isReactComponent = {};
an.prototype.setState = function(e, t) {
  if (typeof e != "object" && typeof e != "function" && e != null) throw Error("setState(...): takes an object of state variables to update or a function which returns an object of state variables.");
  this.updater.enqueueSetState(this, e, t, "setState");
};
an.prototype.forceUpdate = function(e) {
  this.updater.enqueueForceUpdate(this, e, "forceUpdate");
};
function ds() {
}
ds.prototype = an.prototype;
function Xo(e, t, n) {
  this.props = e, this.context = t, this.refs = fs, this.updater = n || as;
}
var Go = Xo.prototype = new ds();
Go.constructor = Xo;
cs(Go, an.prototype);
Go.isPureReactComponent = !0;
var Wi = Array.isArray, ps = Object.prototype.hasOwnProperty, Zo = { current: null }, ms = { key: !0, ref: !0, __self: !0, __source: !0 };
function hs(e, t, n) {
  var r, l = {}, o = null, i = null;
  if (t != null) for (r in t.ref !== void 0 && (i = t.ref), t.key !== void 0 && (o = "" + t.key), t) ps.call(t, r) && !ms.hasOwnProperty(r) && (l[r] = t[r]);
  var u = arguments.length - 2;
  if (u === 1) l.children = n;
  else if (1 < u) {
    for (var s = Array(u), c = 0; c < u; c++) s[c] = arguments[c + 2];
    l.children = s;
  }
  if (e && e.defaultProps) for (r in u = e.defaultProps, u) l[r] === void 0 && (l[r] = u[r]);
  return { $$typeof: er, type: e, key: o, ref: i, props: l, _owner: Zo.current };
}
function Fc(e, t) {
  return { $$typeof: er, type: e.type, key: t, ref: e.ref, props: e.props, _owner: e._owner };
}
function Jo(e) {
  return typeof e == "object" && e !== null && e.$$typeof === er;
}
function Uc(e) {
  var t = { "=": "=0", ":": "=2" };
  return "$" + e.replace(/[=:]/g, function(n) {
    return t[n];
  });
}
var Ki = /\/+/g;
function _l(e, t) {
  return typeof e == "object" && e !== null && e.key != null ? Uc("" + e.key) : t.toString(36);
}
function _r(e, t, n, r, l) {
  var o = typeof e;
  (o === "undefined" || o === "boolean") && (e = null);
  var i = !1;
  if (e === null) i = !0;
  else switch (o) {
    case "string":
    case "number":
      i = !0;
      break;
    case "object":
      switch (e.$$typeof) {
        case er:
        case Nc:
          i = !0;
      }
  }
  if (i) return i = e, l = l(i), e = r === "" ? "." + _l(i, 0) : r, Wi(l) ? (n = "", e != null && (n = e.replace(Ki, "$&/") + "/"), _r(l, t, n, "", function(c) {
    return c;
  })) : l != null && (Jo(l) && (l = Fc(l, n + (!l.key || i && i.key === l.key ? "" : ("" + l.key).replace(Ki, "$&/") + "/") + e)), t.push(l)), 1;
  if (i = 0, r = r === "" ? "." : r + ":", Wi(e)) for (var u = 0; u < e.length; u++) {
    o = e[u];
    var s = r + _l(o, u);
    i += _r(o, t, n, s, l);
  }
  else if (s = Oc(e), typeof s == "function") for (e = s.call(e), u = 0; !(o = e.next()).done; ) o = o.value, s = r + _l(o, u++), i += _r(o, t, n, s, l);
  else if (o === "object") throw t = String(e), Error("Objects are not valid as a React child (found: " + (t === "[object Object]" ? "object with keys {" + Object.keys(e).join(", ") + "}" : t) + "). If you meant to render a collection of children, use an array instead.");
  return i;
}
function sr(e, t, n) {
  if (e == null) return e;
  var r = [], l = 0;
  return _r(e, r, "", "", function(o) {
    return t.call(n, o, l++);
  }), r;
}
function Ac(e) {
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
var de = { current: null }, Nr = { transition: null }, Vc = { ReactCurrentDispatcher: de, ReactCurrentBatchConfig: Nr, ReactCurrentOwner: Zo };
function vs() {
  throw Error("act(...) is not supported in production builds of React.");
}
M.Children = { map: sr, forEach: function(e, t, n) {
  sr(e, function() {
    t.apply(this, arguments);
  }, n);
}, count: function(e) {
  var t = 0;
  return sr(e, function() {
    t++;
  }), t;
}, toArray: function(e) {
  return sr(e, function(t) {
    return t;
  }) || [];
}, only: function(e) {
  if (!Jo(e)) throw Error("React.Children.only expected to receive a single React element child.");
  return e;
} };
M.Component = an;
M.Fragment = Pc;
M.Profiler = Lc;
M.PureComponent = Xo;
M.StrictMode = jc;
M.Suspense = Mc;
M.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED = Vc;
M.act = vs;
M.cloneElement = function(e, t, n) {
  if (e == null) throw Error("React.cloneElement(...): The argument must be a React element, but you passed " + e + ".");
  var r = cs({}, e.props), l = e.key, o = e.ref, i = e._owner;
  if (t != null) {
    if (t.ref !== void 0 && (o = t.ref, i = Zo.current), t.key !== void 0 && (l = "" + t.key), e.type && e.type.defaultProps) var u = e.type.defaultProps;
    for (s in t) ps.call(t, s) && !ms.hasOwnProperty(s) && (r[s] = t[s] === void 0 && u !== void 0 ? u[s] : t[s]);
  }
  var s = arguments.length - 2;
  if (s === 1) r.children = n;
  else if (1 < s) {
    u = Array(s);
    for (var c = 0; c < s; c++) u[c] = arguments[c + 2];
    r.children = u;
  }
  return { $$typeof: er, type: e.type, key: l, ref: o, props: r, _owner: i };
};
M.createContext = function(e) {
  return e = { $$typeof: zc, _currentValue: e, _currentValue2: e, _threadCount: 0, Provider: null, Consumer: null, _defaultValue: null, _globalName: null }, e.Provider = { $$typeof: Tc, _context: e }, e.Consumer = e;
};
M.createElement = hs;
M.createFactory = function(e) {
  var t = hs.bind(null, e);
  return t.type = e, t;
};
M.createRef = function() {
  return { current: null };
};
M.forwardRef = function(e) {
  return { $$typeof: Rc, render: e };
};
M.isValidElement = Jo;
M.lazy = function(e) {
  return { $$typeof: Dc, _payload: { _status: -1, _result: e }, _init: Ac };
};
M.memo = function(e, t) {
  return { $$typeof: Ic, type: e, compare: t === void 0 ? null : t };
};
M.startTransition = function(e) {
  var t = Nr.transition;
  Nr.transition = {};
  try {
    e();
  } finally {
    Nr.transition = t;
  }
};
M.unstable_act = vs;
M.useCallback = function(e, t) {
  return de.current.useCallback(e, t);
};
M.useContext = function(e) {
  return de.current.useContext(e);
};
M.useDebugValue = function() {
};
M.useDeferredValue = function(e) {
  return de.current.useDeferredValue(e);
};
M.useEffect = function(e, t) {
  return de.current.useEffect(e, t);
};
M.useId = function() {
  return de.current.useId();
};
M.useImperativeHandle = function(e, t, n) {
  return de.current.useImperativeHandle(e, t, n);
};
M.useInsertionEffect = function(e, t) {
  return de.current.useInsertionEffect(e, t);
};
M.useLayoutEffect = function(e, t) {
  return de.current.useLayoutEffect(e, t);
};
M.useMemo = function(e, t) {
  return de.current.useMemo(e, t);
};
M.useReducer = function(e, t, n) {
  return de.current.useReducer(e, t, n);
};
M.useRef = function(e) {
  return de.current.useRef(e);
};
M.useState = function(e) {
  return de.current.useState(e);
};
M.useSyncExternalStore = function(e, t, n) {
  return de.current.useSyncExternalStore(e, t, n);
};
M.useTransition = function() {
  return de.current.useTransition();
};
M.version = "18.3.1";
ss.exports = M;
var O = ss.exports;
/**
 * @license React
 * react-jsx-runtime.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var $c = O, Bc = Symbol.for("react.element"), Hc = Symbol.for("react.fragment"), Qc = Object.prototype.hasOwnProperty, Wc = $c.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED.ReactCurrentOwner, Kc = { key: !0, ref: !0, __self: !0, __source: !0 };
function gs(e, t, n) {
  var r, l = {}, o = null, i = null;
  n !== void 0 && (o = "" + n), t.key !== void 0 && (o = "" + t.key), t.ref !== void 0 && (i = t.ref);
  for (r in t) Qc.call(t, r) && !Kc.hasOwnProperty(r) && (l[r] = t[r]);
  if (e && e.defaultProps) for (r in t = e.defaultProps, t) l[r] === void 0 && (l[r] = t[r]);
  return { $$typeof: Bc, type: e, key: o, ref: i, props: l, _owner: Wc.current };
}
ul.Fragment = Hc;
ul.jsx = gs;
ul.jsxs = gs;
us.exports = ul;
var m = us.exports, ys = { exports: {} }, Ee = {}, ws = { exports: {} }, ks = {};
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
    var R = j.length;
    j.push(T);
    e: for (; 0 < R; ) {
      var X = R - 1 >>> 1, b = j[X];
      if (0 < l(b, T)) j[X] = T, j[R] = b, R = X;
      else break e;
    }
  }
  function n(j) {
    return j.length === 0 ? null : j[0];
  }
  function r(j) {
    if (j.length === 0) return null;
    var T = j[0], R = j.pop();
    if (R !== T) {
      j[0] = R;
      e: for (var X = 0, b = j.length, ir = b >>> 1; X < ir; ) {
        var kt = 2 * (X + 1) - 1, El = j[kt], St = kt + 1, ur = j[St];
        if (0 > l(El, R)) St < b && 0 > l(ur, El) ? (j[X] = ur, j[St] = R, X = St) : (j[X] = El, j[kt] = R, X = kt);
        else if (St < b && 0 > l(ur, R)) j[X] = ur, j[St] = R, X = St;
        else break e;
      }
    }
    return T;
  }
  function l(j, T) {
    var R = j.sortIndex - T.sortIndex;
    return R !== 0 ? R : j.id - T.id;
  }
  if (typeof performance == "object" && typeof performance.now == "function") {
    var o = performance;
    e.unstable_now = function() {
      return o.now();
    };
  } else {
    var i = Date, u = i.now();
    e.unstable_now = function() {
      return i.now() - u;
    };
  }
  var s = [], c = [], g = 1, h = null, f = 3, w = !1, k = !1, E = !1, z = typeof setTimeout == "function" ? setTimeout : null, d = typeof clearTimeout == "function" ? clearTimeout : null, a = typeof setImmediate < "u" ? setImmediate : null;
  typeof navigator < "u" && navigator.scheduling !== void 0 && navigator.scheduling.isInputPending !== void 0 && navigator.scheduling.isInputPending.bind(navigator.scheduling);
  function p(j) {
    for (var T = n(c); T !== null; ) {
      if (T.callback === null) r(c);
      else if (T.startTime <= j) r(c), T.sortIndex = T.expirationTime, t(s, T);
      else break;
      T = n(c);
    }
  }
  function v(j) {
    if (E = !1, p(j), !k) if (n(s) !== null) k = !0, pn(C);
    else {
      var T = n(c);
      T !== null && mn(v, T.startTime - j);
    }
  }
  function C(j, T) {
    k = !1, E && (E = !1, d(N), N = -1), w = !0;
    var R = f;
    try {
      for (p(T), h = n(s); h !== null && (!(h.expirationTime > T) || j && !P()); ) {
        var X = h.callback;
        if (typeof X == "function") {
          h.callback = null, f = h.priorityLevel;
          var b = X(h.expirationTime <= T);
          T = e.unstable_now(), typeof b == "function" ? h.callback = b : h === n(s) && r(s), p(T);
        } else r(s);
        h = n(s);
      }
      if (h !== null) var ir = !0;
      else {
        var kt = n(c);
        kt !== null && mn(v, kt.startTime - T), ir = !1;
      }
      return ir;
    } finally {
      h = null, f = R, w = !1;
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
    var dn = new MessageChannel(), or = dn.port2;
    dn.port1.onmessage = A, ae = function() {
      or.postMessage(null);
    };
  } else ae = function() {
    z(A, 0);
  };
  function pn(j) {
    x = j, _ || (_ = !0, ae());
  }
  function mn(j, T) {
    N = z(function() {
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
    var R = f;
    f = T;
    try {
      return j();
    } finally {
      f = R;
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
    var R = f;
    f = j;
    try {
      return T();
    } finally {
      f = R;
    }
  }, e.unstable_scheduleCallback = function(j, T, R) {
    var X = e.unstable_now();
    switch (typeof R == "object" && R !== null ? (R = R.delay, R = typeof R == "number" && 0 < R ? X + R : X) : R = X, j) {
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
    return b = R + b, j = { id: g++, callback: T, priorityLevel: j, startTime: R, expirationTime: b, sortIndex: -1 }, R > X ? (j.sortIndex = R, t(c, j), n(s) === null && j === n(c) && (E ? (d(N), N = -1) : E = !0, mn(v, R - X))) : (j.sortIndex = b, t(s, j), k || w || (k = !0, pn(C))), j;
  }, e.unstable_shouldYield = P, e.unstable_wrapCallback = function(j) {
    var T = f;
    return function() {
      var R = f;
      f = T;
      try {
        return j.apply(this, arguments);
      } finally {
        f = R;
      }
    };
  };
})(ks);
ws.exports = ks;
var Yc = ws.exports;
/**
 * @license React
 * react-dom.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var Xc = O, xe = Yc;
function S(e) {
  for (var t = "https://reactjs.org/docs/error-decoder.html?invariant=" + e, n = 1; n < arguments.length; n++) t += "&args[]=" + encodeURIComponent(arguments[n]);
  return "Minified React error #" + e + "; visit " + t + " for the full message or use the non-minified dev environment for full errors and additional helpful warnings.";
}
var Ss = /* @__PURE__ */ new Set(), On = {};
function It(e, t) {
  tn(e, t), tn(e + "Capture", t);
}
function tn(e, t) {
  for (On[e] = t, e = 0; e < t.length; e++) Ss.add(t[e]);
}
var Ge = !(typeof window > "u" || typeof window.document > "u" || typeof window.document.createElement > "u"), to = Object.prototype.hasOwnProperty, Gc = /^[:A-Z_a-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF\u0370-\u037D\u037F-\u1FFF\u200C-\u200D\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD][:A-Z_a-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF\u0370-\u037D\u037F-\u1FFF\u200C-\u200D\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD\-.0-9\u00B7\u0300-\u036F\u203F-\u2040]*$/, Yi = {}, Xi = {};
function Zc(e) {
  return to.call(Xi, e) ? !0 : to.call(Yi, e) ? !1 : Gc.test(e) ? Xi[e] = !0 : (Yi[e] = !0, !1);
}
function Jc(e, t, n, r) {
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
function qc(e, t, n, r) {
  if (t === null || typeof t > "u" || Jc(e, t, n, r)) return !0;
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
function pe(e, t, n, r, l, o, i) {
  this.acceptsBooleans = t === 2 || t === 3 || t === 4, this.attributeName = r, this.attributeNamespace = l, this.mustUseProperty = n, this.propertyName = e, this.type = t, this.sanitizeURL = o, this.removeEmptyString = i;
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
var qo = /[\-:]([a-z])/g;
function bo(e) {
  return e[1].toUpperCase();
}
"accent-height alignment-baseline arabic-form baseline-shift cap-height clip-path clip-rule color-interpolation color-interpolation-filters color-profile color-rendering dominant-baseline enable-background fill-opacity fill-rule flood-color flood-opacity font-family font-size font-size-adjust font-stretch font-style font-variant font-weight glyph-name glyph-orientation-horizontal glyph-orientation-vertical horiz-adv-x horiz-origin-x image-rendering letter-spacing lighting-color marker-end marker-mid marker-start overline-position overline-thickness paint-order panose-1 pointer-events rendering-intent shape-rendering stop-color stop-opacity strikethrough-position strikethrough-thickness stroke-dasharray stroke-dashoffset stroke-linecap stroke-linejoin stroke-miterlimit stroke-opacity stroke-width text-anchor text-decoration text-rendering underline-position underline-thickness unicode-bidi unicode-range units-per-em v-alphabetic v-hanging v-ideographic v-mathematical vector-effect vert-adv-y vert-origin-x vert-origin-y word-spacing writing-mode xmlns:xlink x-height".split(" ").forEach(function(e) {
  var t = e.replace(
    qo,
    bo
  );
  le[t] = new pe(t, 1, !1, e, null, !1, !1);
});
"xlink:actuate xlink:arcrole xlink:role xlink:show xlink:title xlink:type".split(" ").forEach(function(e) {
  var t = e.replace(qo, bo);
  le[t] = new pe(t, 1, !1, e, "http://www.w3.org/1999/xlink", !1, !1);
});
["xml:base", "xml:lang", "xml:space"].forEach(function(e) {
  var t = e.replace(qo, bo);
  le[t] = new pe(t, 1, !1, e, "http://www.w3.org/XML/1998/namespace", !1, !1);
});
["tabIndex", "crossOrigin"].forEach(function(e) {
  le[e] = new pe(e, 1, !1, e.toLowerCase(), null, !1, !1);
});
le.xlinkHref = new pe("xlinkHref", 1, !1, "xlink:href", "http://www.w3.org/1999/xlink", !0, !1);
["src", "href", "action", "formAction"].forEach(function(e) {
  le[e] = new pe(e, 1, !1, e.toLowerCase(), null, !0, !0);
});
function ei(e, t, n, r) {
  var l = le.hasOwnProperty(t) ? le[t] : null;
  (l !== null ? l.type !== 0 : r || !(2 < t.length) || t[0] !== "o" && t[0] !== "O" || t[1] !== "n" && t[1] !== "N") && (qc(t, n, l, r) && (n = null), r || l === null ? Zc(t) && (n === null ? e.removeAttribute(t) : e.setAttribute(t, "" + n)) : l.mustUseProperty ? e[l.propertyName] = n === null ? l.type === 3 ? !1 : "" : n : (t = l.attributeName, r = l.attributeNamespace, n === null ? e.removeAttribute(t) : (l = l.type, n = l === 3 || l === 4 && n === !0 ? "" : "" + n, r ? e.setAttributeNS(r, t, n) : e.setAttribute(t, n))));
}
var be = Xc.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED, ar = Symbol.for("react.element"), Ft = Symbol.for("react.portal"), Ut = Symbol.for("react.fragment"), ti = Symbol.for("react.strict_mode"), no = Symbol.for("react.profiler"), xs = Symbol.for("react.provider"), Es = Symbol.for("react.context"), ni = Symbol.for("react.forward_ref"), ro = Symbol.for("react.suspense"), lo = Symbol.for("react.suspense_list"), ri = Symbol.for("react.memo"), tt = Symbol.for("react.lazy"), Cs = Symbol.for("react.offscreen"), Gi = Symbol.iterator;
function hn(e) {
  return e === null || typeof e != "object" ? null : (e = Gi && e[Gi] || e["@@iterator"], typeof e == "function" ? e : null);
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
`), o = r.stack.split(`
`), i = l.length - 1, u = o.length - 1; 1 <= i && 0 <= u && l[i] !== o[u]; ) u--;
      for (; 1 <= i && 0 <= u; i--, u--) if (l[i] !== o[u]) {
        if (i !== 1 || u !== 1)
          do
            if (i--, u--, 0 > u || l[i] !== o[u]) {
              var s = `
` + l[i].replace(" at new ", " at ");
              return e.displayName && s.includes("<anonymous>") && (s = s.replace("<anonymous>", e.displayName)), s;
            }
          while (1 <= i && 0 <= u);
        break;
      }
    }
  } finally {
    Pl = !1, Error.prepareStackTrace = n;
  }
  return (e = e ? e.displayName || e.name : "") ? En(e) : "";
}
function bc(e) {
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
function oo(e) {
  if (e == null) return null;
  if (typeof e == "function") return e.displayName || e.name || null;
  if (typeof e == "string") return e;
  switch (e) {
    case Ut:
      return "Fragment";
    case Ft:
      return "Portal";
    case no:
      return "Profiler";
    case ti:
      return "StrictMode";
    case ro:
      return "Suspense";
    case lo:
      return "SuspenseList";
  }
  if (typeof e == "object") switch (e.$$typeof) {
    case Es:
      return (e.displayName || "Context") + ".Consumer";
    case xs:
      return (e._context.displayName || "Context") + ".Provider";
    case ni:
      var t = e.render;
      return e = e.displayName, e || (e = t.displayName || t.name || "", e = e !== "" ? "ForwardRef(" + e + ")" : "ForwardRef"), e;
    case ri:
      return t = e.displayName || null, t !== null ? t : oo(e.type) || "Memo";
    case tt:
      t = e._payload, e = e._init;
      try {
        return oo(e(t));
      } catch {
      }
  }
  return null;
}
function ef(e) {
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
      return oo(t);
    case 8:
      return t === ti ? "StrictMode" : "Mode";
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
function _s(e) {
  var t = e.type;
  return (e = e.nodeName) && e.toLowerCase() === "input" && (t === "checkbox" || t === "radio");
}
function tf(e) {
  var t = _s(e) ? "checked" : "value", n = Object.getOwnPropertyDescriptor(e.constructor.prototype, t), r = "" + e[t];
  if (!e.hasOwnProperty(t) && typeof n < "u" && typeof n.get == "function" && typeof n.set == "function") {
    var l = n.get, o = n.set;
    return Object.defineProperty(e, t, { configurable: !0, get: function() {
      return l.call(this);
    }, set: function(i) {
      r = "" + i, o.call(this, i);
    } }), Object.defineProperty(e, t, { enumerable: n.enumerable }), { getValue: function() {
      return r;
    }, setValue: function(i) {
      r = "" + i;
    }, stopTracking: function() {
      e._valueTracker = null, delete e[t];
    } };
  }
}
function cr(e) {
  e._valueTracker || (e._valueTracker = tf(e));
}
function Ns(e) {
  if (!e) return !1;
  var t = e._valueTracker;
  if (!t) return !0;
  var n = t.getValue(), r = "";
  return e && (r = _s(e) ? e.checked ? "true" : "false" : e.value), e = r, e !== n ? (t.setValue(e), !0) : !1;
}
function Fr(e) {
  if (e = e || (typeof document < "u" ? document : void 0), typeof e > "u") return null;
  try {
    return e.activeElement || e.body;
  } catch {
    return e.body;
  }
}
function io(e, t) {
  var n = t.checked;
  return K({}, t, { defaultChecked: void 0, defaultValue: void 0, value: void 0, checked: n ?? e._wrapperState.initialChecked });
}
function Zi(e, t) {
  var n = t.defaultValue == null ? "" : t.defaultValue, r = t.checked != null ? t.checked : t.defaultChecked;
  n = ht(t.value != null ? t.value : n), e._wrapperState = { initialChecked: r, initialValue: n, controlled: t.type === "checkbox" || t.type === "radio" ? t.checked != null : t.value != null };
}
function Ps(e, t) {
  t = t.checked, t != null && ei(e, "checked", t, !1);
}
function uo(e, t) {
  Ps(e, t);
  var n = ht(t.value), r = t.type;
  if (n != null) r === "number" ? (n === 0 && e.value === "" || e.value != n) && (e.value = "" + n) : e.value !== "" + n && (e.value = "" + n);
  else if (r === "submit" || r === "reset") {
    e.removeAttribute("value");
    return;
  }
  t.hasOwnProperty("value") ? so(e, t.type, n) : t.hasOwnProperty("defaultValue") && so(e, t.type, ht(t.defaultValue)), t.checked == null && t.defaultChecked != null && (e.defaultChecked = !!t.defaultChecked);
}
function Ji(e, t, n) {
  if (t.hasOwnProperty("value") || t.hasOwnProperty("defaultValue")) {
    var r = t.type;
    if (!(r !== "submit" && r !== "reset" || t.value !== void 0 && t.value !== null)) return;
    t = "" + e._wrapperState.initialValue, n || t === e.value || (e.value = t), e.defaultValue = t;
  }
  n = e.name, n !== "" && (e.name = ""), e.defaultChecked = !!e._wrapperState.initialChecked, n !== "" && (e.name = n);
}
function so(e, t, n) {
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
function ao(e, t) {
  if (t.dangerouslySetInnerHTML != null) throw Error(S(91));
  return K({}, t, { value: void 0, defaultValue: void 0, children: "" + e._wrapperState.initialValue });
}
function qi(e, t) {
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
function js(e, t) {
  var n = ht(t.value), r = ht(t.defaultValue);
  n != null && (n = "" + n, n !== e.value && (e.value = n), t.defaultValue == null && e.defaultValue !== n && (e.defaultValue = n)), r != null && (e.defaultValue = "" + r);
}
function bi(e) {
  var t = e.textContent;
  t === e._wrapperState.initialValue && t !== "" && t !== null && (e.value = t);
}
function Ls(e) {
  switch (e) {
    case "svg":
      return "http://www.w3.org/2000/svg";
    case "math":
      return "http://www.w3.org/1998/Math/MathML";
    default:
      return "http://www.w3.org/1999/xhtml";
  }
}
function co(e, t) {
  return e == null || e === "http://www.w3.org/1999/xhtml" ? Ls(t) : e === "http://www.w3.org/2000/svg" && t === "foreignObject" ? "http://www.w3.org/1999/xhtml" : e;
}
var fr, Ts = function(e) {
  return typeof MSApp < "u" && MSApp.execUnsafeLocalFunction ? function(t, n, r, l) {
    MSApp.execUnsafeLocalFunction(function() {
      return e(t, n, r, l);
    });
  } : e;
}(function(e, t) {
  if (e.namespaceURI !== "http://www.w3.org/2000/svg" || "innerHTML" in e) e.innerHTML = t;
  else {
    for (fr = fr || document.createElement("div"), fr.innerHTML = "<svg>" + t.valueOf().toString() + "</svg>", t = fr.firstChild; e.firstChild; ) e.removeChild(e.firstChild);
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
}, nf = ["Webkit", "ms", "Moz", "O"];
Object.keys(Pn).forEach(function(e) {
  nf.forEach(function(t) {
    t = t + e.charAt(0).toUpperCase() + e.substring(1), Pn[t] = Pn[e];
  });
});
function zs(e, t, n) {
  return t == null || typeof t == "boolean" || t === "" ? "" : n || typeof t != "number" || t === 0 || Pn.hasOwnProperty(e) && Pn[e] ? ("" + t).trim() : t + "px";
}
function Rs(e, t) {
  e = e.style;
  for (var n in t) if (t.hasOwnProperty(n)) {
    var r = n.indexOf("--") === 0, l = zs(n, t[n], r);
    n === "float" && (n = "cssFloat"), r ? e.setProperty(n, l) : e[n] = l;
  }
}
var rf = K({ menuitem: !0 }, { area: !0, base: !0, br: !0, col: !0, embed: !0, hr: !0, img: !0, input: !0, keygen: !0, link: !0, meta: !0, param: !0, source: !0, track: !0, wbr: !0 });
function fo(e, t) {
  if (t) {
    if (rf[e] && (t.children != null || t.dangerouslySetInnerHTML != null)) throw Error(S(137, e));
    if (t.dangerouslySetInnerHTML != null) {
      if (t.children != null) throw Error(S(60));
      if (typeof t.dangerouslySetInnerHTML != "object" || !("__html" in t.dangerouslySetInnerHTML)) throw Error(S(61));
    }
    if (t.style != null && typeof t.style != "object") throw Error(S(62));
  }
}
function po(e, t) {
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
var mo = null;
function li(e) {
  return e = e.target || e.srcElement || window, e.correspondingUseElement && (e = e.correspondingUseElement), e.nodeType === 3 ? e.parentNode : e;
}
var ho = null, Zt = null, Jt = null;
function eu(e) {
  if (e = rr(e)) {
    if (typeof ho != "function") throw Error(S(280));
    var t = e.stateNode;
    t && (t = dl(t), ho(e.stateNode, e.type, t));
  }
}
function Ms(e) {
  Zt ? Jt ? Jt.push(e) : Jt = [e] : Zt = e;
}
function Is() {
  if (Zt) {
    var e = Zt, t = Jt;
    if (Jt = Zt = null, eu(e), t) for (e = 0; e < t.length; e++) eu(t[e]);
  }
}
function Ds(e, t) {
  return e(t);
}
function Os() {
}
var Ll = !1;
function Fs(e, t, n) {
  if (Ll) return e(t, n);
  Ll = !0;
  try {
    return Ds(e, t, n);
  } finally {
    Ll = !1, (Zt !== null || Jt !== null) && (Os(), Is());
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
var vo = !1;
if (Ge) try {
  var vn = {};
  Object.defineProperty(vn, "passive", { get: function() {
    vo = !0;
  } }), window.addEventListener("test", vn, vn), window.removeEventListener("test", vn, vn);
} catch {
  vo = !1;
}
function lf(e, t, n, r, l, o, i, u, s) {
  var c = Array.prototype.slice.call(arguments, 3);
  try {
    t.apply(n, c);
  } catch (g) {
    this.onError(g);
  }
}
var jn = !1, Ur = null, Ar = !1, go = null, of = { onError: function(e) {
  jn = !0, Ur = e;
} };
function uf(e, t, n, r, l, o, i, u, s) {
  jn = !1, Ur = null, lf.apply(of, arguments);
}
function sf(e, t, n, r, l, o, i, u, s) {
  if (uf.apply(this, arguments), jn) {
    if (jn) {
      var c = Ur;
      jn = !1, Ur = null;
    } else throw Error(S(198));
    Ar || (Ar = !0, go = c);
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
function Us(e) {
  if (e.tag === 13) {
    var t = e.memoizedState;
    if (t === null && (e = e.alternate, e !== null && (t = e.memoizedState)), t !== null) return t.dehydrated;
  }
  return null;
}
function tu(e) {
  if (Dt(e) !== e) throw Error(S(188));
}
function af(e) {
  var t = e.alternate;
  if (!t) {
    if (t = Dt(e), t === null) throw Error(S(188));
    return t !== e ? null : e;
  }
  for (var n = e, r = t; ; ) {
    var l = n.return;
    if (l === null) break;
    var o = l.alternate;
    if (o === null) {
      if (r = l.return, r !== null) {
        n = r;
        continue;
      }
      break;
    }
    if (l.child === o.child) {
      for (o = l.child; o; ) {
        if (o === n) return tu(l), e;
        if (o === r) return tu(l), t;
        o = o.sibling;
      }
      throw Error(S(188));
    }
    if (n.return !== r.return) n = l, r = o;
    else {
      for (var i = !1, u = l.child; u; ) {
        if (u === n) {
          i = !0, n = l, r = o;
          break;
        }
        if (u === r) {
          i = !0, r = l, n = o;
          break;
        }
        u = u.sibling;
      }
      if (!i) {
        for (u = o.child; u; ) {
          if (u === n) {
            i = !0, n = o, r = l;
            break;
          }
          if (u === r) {
            i = !0, r = o, n = l;
            break;
          }
          u = u.sibling;
        }
        if (!i) throw Error(S(189));
      }
    }
    if (n.alternate !== r) throw Error(S(190));
  }
  if (n.tag !== 3) throw Error(S(188));
  return n.stateNode.current === n ? e : t;
}
function As(e) {
  return e = af(e), e !== null ? Vs(e) : null;
}
function Vs(e) {
  if (e.tag === 5 || e.tag === 6) return e;
  for (e = e.child; e !== null; ) {
    var t = Vs(e);
    if (t !== null) return t;
    e = e.sibling;
  }
  return null;
}
var $s = xe.unstable_scheduleCallback, nu = xe.unstable_cancelCallback, cf = xe.unstable_shouldYield, ff = xe.unstable_requestPaint, G = xe.unstable_now, df = xe.unstable_getCurrentPriorityLevel, oi = xe.unstable_ImmediatePriority, Bs = xe.unstable_UserBlockingPriority, Vr = xe.unstable_NormalPriority, pf = xe.unstable_LowPriority, Hs = xe.unstable_IdlePriority, sl = null, Be = null;
function mf(e) {
  if (Be && typeof Be.onCommitFiberRoot == "function") try {
    Be.onCommitFiberRoot(sl, e, void 0, (e.current.flags & 128) === 128);
  } catch {
  }
}
var Oe = Math.clz32 ? Math.clz32 : gf, hf = Math.log, vf = Math.LN2;
function gf(e) {
  return e >>>= 0, e === 0 ? 32 : 31 - (hf(e) / vf | 0) | 0;
}
var dr = 64, pr = 4194304;
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
  var r = 0, l = e.suspendedLanes, o = e.pingedLanes, i = n & 268435455;
  if (i !== 0) {
    var u = i & ~l;
    u !== 0 ? r = _n(u) : (o &= i, o !== 0 && (r = _n(o)));
  } else i = n & ~l, i !== 0 ? r = _n(i) : o !== 0 && (r = _n(o));
  if (r === 0) return 0;
  if (t !== 0 && t !== r && !(t & l) && (l = r & -r, o = t & -t, l >= o || l === 16 && (o & 4194240) !== 0)) return t;
  if (r & 4 && (r |= n & 16), t = e.entangledLanes, t !== 0) for (e = e.entanglements, t &= r; 0 < t; ) n = 31 - Oe(t), l = 1 << n, r |= e[n], t &= ~l;
  return r;
}
function yf(e, t) {
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
function wf(e, t) {
  for (var n = e.suspendedLanes, r = e.pingedLanes, l = e.expirationTimes, o = e.pendingLanes; 0 < o; ) {
    var i = 31 - Oe(o), u = 1 << i, s = l[i];
    s === -1 ? (!(u & n) || u & r) && (l[i] = yf(u, t)) : s <= t && (e.expiredLanes |= u), o &= ~u;
  }
}
function yo(e) {
  return e = e.pendingLanes & -1073741825, e !== 0 ? e : e & 1073741824 ? 1073741824 : 0;
}
function Qs() {
  var e = dr;
  return dr <<= 1, !(dr & 4194240) && (dr = 64), e;
}
function Tl(e) {
  for (var t = [], n = 0; 31 > n; n++) t.push(e);
  return t;
}
function tr(e, t, n) {
  e.pendingLanes |= t, t !== 536870912 && (e.suspendedLanes = 0, e.pingedLanes = 0), e = e.eventTimes, t = 31 - Oe(t), e[t] = n;
}
function kf(e, t) {
  var n = e.pendingLanes & ~t;
  e.pendingLanes = t, e.suspendedLanes = 0, e.pingedLanes = 0, e.expiredLanes &= t, e.mutableReadLanes &= t, e.entangledLanes &= t, t = e.entanglements;
  var r = e.eventTimes;
  for (e = e.expirationTimes; 0 < n; ) {
    var l = 31 - Oe(n), o = 1 << l;
    t[l] = 0, r[l] = -1, e[l] = -1, n &= ~o;
  }
}
function ii(e, t) {
  var n = e.entangledLanes |= t;
  for (e = e.entanglements; n; ) {
    var r = 31 - Oe(n), l = 1 << r;
    l & t | e[r] & t && (e[r] |= t), n &= ~l;
  }
}
var F = 0;
function Ws(e) {
  return e &= -e, 1 < e ? 4 < e ? e & 268435455 ? 16 : 536870912 : 4 : 1;
}
var Ks, ui, Ys, Xs, Gs, wo = !1, mr = [], ut = null, st = null, at = null, An = /* @__PURE__ */ new Map(), Vn = /* @__PURE__ */ new Map(), rt = [], Sf = "mousedown mouseup touchcancel touchend touchstart auxclick dblclick pointercancel pointerdown pointerup dragend dragstart drop compositionend compositionstart keydown keypress keyup input textInput copy cut paste click change contextmenu reset submit".split(" ");
function ru(e, t) {
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
function gn(e, t, n, r, l, o) {
  return e === null || e.nativeEvent !== o ? (e = { blockedOn: t, domEventName: n, eventSystemFlags: r, nativeEvent: o, targetContainers: [l] }, t !== null && (t = rr(t), t !== null && ui(t)), e) : (e.eventSystemFlags |= r, t = e.targetContainers, l !== null && t.indexOf(l) === -1 && t.push(l), e);
}
function xf(e, t, n, r, l) {
  switch (t) {
    case "focusin":
      return ut = gn(ut, e, t, n, r, l), !0;
    case "dragenter":
      return st = gn(st, e, t, n, r, l), !0;
    case "mouseover":
      return at = gn(at, e, t, n, r, l), !0;
    case "pointerover":
      var o = l.pointerId;
      return An.set(o, gn(An.get(o) || null, e, t, n, r, l)), !0;
    case "gotpointercapture":
      return o = l.pointerId, Vn.set(o, gn(Vn.get(o) || null, e, t, n, r, l)), !0;
  }
  return !1;
}
function Zs(e) {
  var t = Ct(e.target);
  if (t !== null) {
    var n = Dt(t);
    if (n !== null) {
      if (t = n.tag, t === 13) {
        if (t = Us(n), t !== null) {
          e.blockedOn = t, Gs(e.priority, function() {
            Ys(n);
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
function Pr(e) {
  if (e.blockedOn !== null) return !1;
  for (var t = e.targetContainers; 0 < t.length; ) {
    var n = ko(e.domEventName, e.eventSystemFlags, t[0], e.nativeEvent);
    if (n === null) {
      n = e.nativeEvent;
      var r = new n.constructor(n.type, n);
      mo = r, n.target.dispatchEvent(r), mo = null;
    } else return t = rr(n), t !== null && ui(t), e.blockedOn = n, !1;
    t.shift();
  }
  return !0;
}
function lu(e, t, n) {
  Pr(e) && n.delete(t);
}
function Ef() {
  wo = !1, ut !== null && Pr(ut) && (ut = null), st !== null && Pr(st) && (st = null), at !== null && Pr(at) && (at = null), An.forEach(lu), Vn.forEach(lu);
}
function yn(e, t) {
  e.blockedOn === t && (e.blockedOn = null, wo || (wo = !0, xe.unstable_scheduleCallback(xe.unstable_NormalPriority, Ef)));
}
function $n(e) {
  function t(l) {
    return yn(l, e);
  }
  if (0 < mr.length) {
    yn(mr[0], e);
    for (var n = 1; n < mr.length; n++) {
      var r = mr[n];
      r.blockedOn === e && (r.blockedOn = null);
    }
  }
  for (ut !== null && yn(ut, e), st !== null && yn(st, e), at !== null && yn(at, e), An.forEach(t), Vn.forEach(t), n = 0; n < rt.length; n++) r = rt[n], r.blockedOn === e && (r.blockedOn = null);
  for (; 0 < rt.length && (n = rt[0], n.blockedOn === null); ) Zs(n), n.blockedOn === null && rt.shift();
}
var qt = be.ReactCurrentBatchConfig, Br = !0;
function Cf(e, t, n, r) {
  var l = F, o = qt.transition;
  qt.transition = null;
  try {
    F = 1, si(e, t, n, r);
  } finally {
    F = l, qt.transition = o;
  }
}
function _f(e, t, n, r) {
  var l = F, o = qt.transition;
  qt.transition = null;
  try {
    F = 4, si(e, t, n, r);
  } finally {
    F = l, qt.transition = o;
  }
}
function si(e, t, n, r) {
  if (Br) {
    var l = ko(e, t, n, r);
    if (l === null) Vl(e, t, r, Hr, n), ru(e, r);
    else if (xf(l, e, t, n, r)) r.stopPropagation();
    else if (ru(e, r), t & 4 && -1 < Sf.indexOf(e)) {
      for (; l !== null; ) {
        var o = rr(l);
        if (o !== null && Ks(o), o = ko(e, t, n, r), o === null && Vl(e, t, r, Hr, n), o === l) break;
        l = o;
      }
      l !== null && r.stopPropagation();
    } else Vl(e, t, r, null, n);
  }
}
var Hr = null;
function ko(e, t, n, r) {
  if (Hr = null, e = li(r), e = Ct(e), e !== null) if (t = Dt(e), t === null) e = null;
  else if (n = t.tag, n === 13) {
    if (e = Us(t), e !== null) return e;
    e = null;
  } else if (n === 3) {
    if (t.stateNode.current.memoizedState.isDehydrated) return t.tag === 3 ? t.stateNode.containerInfo : null;
    e = null;
  } else t !== e && (e = null);
  return Hr = e, null;
}
function Js(e) {
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
      switch (df()) {
        case oi:
          return 1;
        case Bs:
          return 4;
        case Vr:
        case pf:
          return 16;
        case Hs:
          return 536870912;
        default:
          return 16;
      }
    default:
      return 16;
  }
}
var ot = null, ai = null, jr = null;
function qs() {
  if (jr) return jr;
  var e, t = ai, n = t.length, r, l = "value" in ot ? ot.value : ot.textContent, o = l.length;
  for (e = 0; e < n && t[e] === l[e]; e++) ;
  var i = n - e;
  for (r = 1; r <= i && t[n - r] === l[o - r]; r++) ;
  return jr = l.slice(e, 1 < r ? 1 - r : void 0);
}
function Lr(e) {
  var t = e.keyCode;
  return "charCode" in e ? (e = e.charCode, e === 0 && t === 13 && (e = 13)) : e = t, e === 10 && (e = 13), 32 <= e || e === 13 ? e : 0;
}
function hr() {
  return !0;
}
function ou() {
  return !1;
}
function Ce(e) {
  function t(n, r, l, o, i) {
    this._reactName = n, this._targetInst = l, this.type = r, this.nativeEvent = o, this.target = i, this.currentTarget = null;
    for (var u in e) e.hasOwnProperty(u) && (n = e[u], this[u] = n ? n(o) : o[u]);
    return this.isDefaultPrevented = (o.defaultPrevented != null ? o.defaultPrevented : o.returnValue === !1) ? hr : ou, this.isPropagationStopped = ou, this;
  }
  return K(t.prototype, { preventDefault: function() {
    this.defaultPrevented = !0;
    var n = this.nativeEvent;
    n && (n.preventDefault ? n.preventDefault() : typeof n.returnValue != "unknown" && (n.returnValue = !1), this.isDefaultPrevented = hr);
  }, stopPropagation: function() {
    var n = this.nativeEvent;
    n && (n.stopPropagation ? n.stopPropagation() : typeof n.cancelBubble != "unknown" && (n.cancelBubble = !0), this.isPropagationStopped = hr);
  }, persist: function() {
  }, isPersistent: hr }), t;
}
var cn = { eventPhase: 0, bubbles: 0, cancelable: 0, timeStamp: function(e) {
  return e.timeStamp || Date.now();
}, defaultPrevented: 0, isTrusted: 0 }, ci = Ce(cn), nr = K({}, cn, { view: 0, detail: 0 }), Nf = Ce(nr), zl, Rl, wn, al = K({}, nr, { screenX: 0, screenY: 0, clientX: 0, clientY: 0, pageX: 0, pageY: 0, ctrlKey: 0, shiftKey: 0, altKey: 0, metaKey: 0, getModifierState: fi, button: 0, buttons: 0, relatedTarget: function(e) {
  return e.relatedTarget === void 0 ? e.fromElement === e.srcElement ? e.toElement : e.fromElement : e.relatedTarget;
}, movementX: function(e) {
  return "movementX" in e ? e.movementX : (e !== wn && (wn && e.type === "mousemove" ? (zl = e.screenX - wn.screenX, Rl = e.screenY - wn.screenY) : Rl = zl = 0, wn = e), zl);
}, movementY: function(e) {
  return "movementY" in e ? e.movementY : Rl;
} }), iu = Ce(al), Pf = K({}, al, { dataTransfer: 0 }), jf = Ce(Pf), Lf = K({}, nr, { relatedTarget: 0 }), Ml = Ce(Lf), Tf = K({}, cn, { animationName: 0, elapsedTime: 0, pseudoElement: 0 }), zf = Ce(Tf), Rf = K({}, cn, { clipboardData: function(e) {
  return "clipboardData" in e ? e.clipboardData : window.clipboardData;
} }), Mf = Ce(Rf), If = K({}, cn, { data: 0 }), uu = Ce(If), Df = {
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
}, Of = {
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
}, Ff = { Alt: "altKey", Control: "ctrlKey", Meta: "metaKey", Shift: "shiftKey" };
function Uf(e) {
  var t = this.nativeEvent;
  return t.getModifierState ? t.getModifierState(e) : (e = Ff[e]) ? !!t[e] : !1;
}
function fi() {
  return Uf;
}
var Af = K({}, nr, { key: function(e) {
  if (e.key) {
    var t = Df[e.key] || e.key;
    if (t !== "Unidentified") return t;
  }
  return e.type === "keypress" ? (e = Lr(e), e === 13 ? "Enter" : String.fromCharCode(e)) : e.type === "keydown" || e.type === "keyup" ? Of[e.keyCode] || "Unidentified" : "";
}, code: 0, location: 0, ctrlKey: 0, shiftKey: 0, altKey: 0, metaKey: 0, repeat: 0, locale: 0, getModifierState: fi, charCode: function(e) {
  return e.type === "keypress" ? Lr(e) : 0;
}, keyCode: function(e) {
  return e.type === "keydown" || e.type === "keyup" ? e.keyCode : 0;
}, which: function(e) {
  return e.type === "keypress" ? Lr(e) : e.type === "keydown" || e.type === "keyup" ? e.keyCode : 0;
} }), Vf = Ce(Af), $f = K({}, al, { pointerId: 0, width: 0, height: 0, pressure: 0, tangentialPressure: 0, tiltX: 0, tiltY: 0, twist: 0, pointerType: 0, isPrimary: 0 }), su = Ce($f), Bf = K({}, nr, { touches: 0, targetTouches: 0, changedTouches: 0, altKey: 0, metaKey: 0, ctrlKey: 0, shiftKey: 0, getModifierState: fi }), Hf = Ce(Bf), Qf = K({}, cn, { propertyName: 0, elapsedTime: 0, pseudoElement: 0 }), Wf = Ce(Qf), Kf = K({}, al, {
  deltaX: function(e) {
    return "deltaX" in e ? e.deltaX : "wheelDeltaX" in e ? -e.wheelDeltaX : 0;
  },
  deltaY: function(e) {
    return "deltaY" in e ? e.deltaY : "wheelDeltaY" in e ? -e.wheelDeltaY : "wheelDelta" in e ? -e.wheelDelta : 0;
  },
  deltaZ: 0,
  deltaMode: 0
}), Yf = Ce(Kf), Xf = [9, 13, 27, 32], di = Ge && "CompositionEvent" in window, Ln = null;
Ge && "documentMode" in document && (Ln = document.documentMode);
var Gf = Ge && "TextEvent" in window && !Ln, bs = Ge && (!di || Ln && 8 < Ln && 11 >= Ln), au = " ", cu = !1;
function ea(e, t) {
  switch (e) {
    case "keyup":
      return Xf.indexOf(t.keyCode) !== -1;
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
function ta(e) {
  return e = e.detail, typeof e == "object" && "data" in e ? e.data : null;
}
var At = !1;
function Zf(e, t) {
  switch (e) {
    case "compositionend":
      return ta(t);
    case "keypress":
      return t.which !== 32 ? null : (cu = !0, au);
    case "textInput":
      return e = t.data, e === au && cu ? null : e;
    default:
      return null;
  }
}
function Jf(e, t) {
  if (At) return e === "compositionend" || !di && ea(e, t) ? (e = qs(), jr = ai = ot = null, At = !1, e) : null;
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
      return bs && t.locale !== "ko" ? null : t.data;
    default:
      return null;
  }
}
var qf = { color: !0, date: !0, datetime: !0, "datetime-local": !0, email: !0, month: !0, number: !0, password: !0, range: !0, search: !0, tel: !0, text: !0, time: !0, url: !0, week: !0 };
function fu(e) {
  var t = e && e.nodeName && e.nodeName.toLowerCase();
  return t === "input" ? !!qf[e.type] : t === "textarea";
}
function na(e, t, n, r) {
  Ms(r), t = Qr(t, "onChange"), 0 < t.length && (n = new ci("onChange", "change", null, n, r), e.push({ event: n, listeners: t }));
}
var Tn = null, Bn = null;
function bf(e) {
  pa(e, 0);
}
function cl(e) {
  var t = Bt(e);
  if (Ns(t)) return e;
}
function ed(e, t) {
  if (e === "change") return t;
}
var ra = !1;
if (Ge) {
  var Il;
  if (Ge) {
    var Dl = "oninput" in document;
    if (!Dl) {
      var du = document.createElement("div");
      du.setAttribute("oninput", "return;"), Dl = typeof du.oninput == "function";
    }
    Il = Dl;
  } else Il = !1;
  ra = Il && (!document.documentMode || 9 < document.documentMode);
}
function pu() {
  Tn && (Tn.detachEvent("onpropertychange", la), Bn = Tn = null);
}
function la(e) {
  if (e.propertyName === "value" && cl(Bn)) {
    var t = [];
    na(t, Bn, e, li(e)), Fs(bf, t);
  }
}
function td(e, t, n) {
  e === "focusin" ? (pu(), Tn = t, Bn = n, Tn.attachEvent("onpropertychange", la)) : e === "focusout" && pu();
}
function nd(e) {
  if (e === "selectionchange" || e === "keyup" || e === "keydown") return cl(Bn);
}
function rd(e, t) {
  if (e === "click") return cl(t);
}
function ld(e, t) {
  if (e === "input" || e === "change") return cl(t);
}
function od(e, t) {
  return e === t && (e !== 0 || 1 / e === 1 / t) || e !== e && t !== t;
}
var Ue = typeof Object.is == "function" ? Object.is : od;
function Hn(e, t) {
  if (Ue(e, t)) return !0;
  if (typeof e != "object" || e === null || typeof t != "object" || t === null) return !1;
  var n = Object.keys(e), r = Object.keys(t);
  if (n.length !== r.length) return !1;
  for (r = 0; r < n.length; r++) {
    var l = n[r];
    if (!to.call(t, l) || !Ue(e[l], t[l])) return !1;
  }
  return !0;
}
function mu(e) {
  for (; e && e.firstChild; ) e = e.firstChild;
  return e;
}
function hu(e, t) {
  var n = mu(e);
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
    n = mu(n);
  }
}
function oa(e, t) {
  return e && t ? e === t ? !0 : e && e.nodeType === 3 ? !1 : t && t.nodeType === 3 ? oa(e, t.parentNode) : "contains" in e ? e.contains(t) : e.compareDocumentPosition ? !!(e.compareDocumentPosition(t) & 16) : !1 : !1;
}
function ia() {
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
function pi(e) {
  var t = e && e.nodeName && e.nodeName.toLowerCase();
  return t && (t === "input" && (e.type === "text" || e.type === "search" || e.type === "tel" || e.type === "url" || e.type === "password") || t === "textarea" || e.contentEditable === "true");
}
function id(e) {
  var t = ia(), n = e.focusedElem, r = e.selectionRange;
  if (t !== n && n && n.ownerDocument && oa(n.ownerDocument.documentElement, n)) {
    if (r !== null && pi(n)) {
      if (t = r.start, e = r.end, e === void 0 && (e = t), "selectionStart" in n) n.selectionStart = t, n.selectionEnd = Math.min(e, n.value.length);
      else if (e = (t = n.ownerDocument || document) && t.defaultView || window, e.getSelection) {
        e = e.getSelection();
        var l = n.textContent.length, o = Math.min(r.start, l);
        r = r.end === void 0 ? o : Math.min(r.end, l), !e.extend && o > r && (l = r, r = o, o = l), l = hu(n, o);
        var i = hu(
          n,
          r
        );
        l && i && (e.rangeCount !== 1 || e.anchorNode !== l.node || e.anchorOffset !== l.offset || e.focusNode !== i.node || e.focusOffset !== i.offset) && (t = t.createRange(), t.setStart(l.node, l.offset), e.removeAllRanges(), o > r ? (e.addRange(t), e.extend(i.node, i.offset)) : (t.setEnd(i.node, i.offset), e.addRange(t)));
      }
    }
    for (t = [], e = n; e = e.parentNode; ) e.nodeType === 1 && t.push({ element: e, left: e.scrollLeft, top: e.scrollTop });
    for (typeof n.focus == "function" && n.focus(), n = 0; n < t.length; n++) e = t[n], e.element.scrollLeft = e.left, e.element.scrollTop = e.top;
  }
}
var ud = Ge && "documentMode" in document && 11 >= document.documentMode, Vt = null, So = null, zn = null, xo = !1;
function vu(e, t, n) {
  var r = n.window === n ? n.document : n.nodeType === 9 ? n : n.ownerDocument;
  xo || Vt == null || Vt !== Fr(r) || (r = Vt, "selectionStart" in r && pi(r) ? r = { start: r.selectionStart, end: r.selectionEnd } : (r = (r.ownerDocument && r.ownerDocument.defaultView || window).getSelection(), r = { anchorNode: r.anchorNode, anchorOffset: r.anchorOffset, focusNode: r.focusNode, focusOffset: r.focusOffset }), zn && Hn(zn, r) || (zn = r, r = Qr(So, "onSelect"), 0 < r.length && (t = new ci("onSelect", "select", null, t, n), e.push({ event: t, listeners: r }), t.target = Vt)));
}
function vr(e, t) {
  var n = {};
  return n[e.toLowerCase()] = t.toLowerCase(), n["Webkit" + e] = "webkit" + t, n["Moz" + e] = "moz" + t, n;
}
var $t = { animationend: vr("Animation", "AnimationEnd"), animationiteration: vr("Animation", "AnimationIteration"), animationstart: vr("Animation", "AnimationStart"), transitionend: vr("Transition", "TransitionEnd") }, Ol = {}, ua = {};
Ge && (ua = document.createElement("div").style, "AnimationEvent" in window || (delete $t.animationend.animation, delete $t.animationiteration.animation, delete $t.animationstart.animation), "TransitionEvent" in window || delete $t.transitionend.transition);
function fl(e) {
  if (Ol[e]) return Ol[e];
  if (!$t[e]) return e;
  var t = $t[e], n;
  for (n in t) if (t.hasOwnProperty(n) && n in ua) return Ol[e] = t[n];
  return e;
}
var sa = fl("animationend"), aa = fl("animationiteration"), ca = fl("animationstart"), fa = fl("transitionend"), da = /* @__PURE__ */ new Map(), gu = "abort auxClick cancel canPlay canPlayThrough click close contextMenu copy cut drag dragEnd dragEnter dragExit dragLeave dragOver dragStart drop durationChange emptied encrypted ended error gotPointerCapture input invalid keyDown keyPress keyUp load loadedData loadedMetadata loadStart lostPointerCapture mouseDown mouseMove mouseOut mouseOver mouseUp paste pause play playing pointerCancel pointerDown pointerMove pointerOut pointerOver pointerUp progress rateChange reset resize seeked seeking stalled submit suspend timeUpdate touchCancel touchEnd touchStart volumeChange scroll toggle touchMove waiting wheel".split(" ");
function gt(e, t) {
  da.set(e, t), It(t, [e]);
}
for (var Fl = 0; Fl < gu.length; Fl++) {
  var Ul = gu[Fl], sd = Ul.toLowerCase(), ad = Ul[0].toUpperCase() + Ul.slice(1);
  gt(sd, "on" + ad);
}
gt(sa, "onAnimationEnd");
gt(aa, "onAnimationIteration");
gt(ca, "onAnimationStart");
gt("dblclick", "onDoubleClick");
gt("focusin", "onFocus");
gt("focusout", "onBlur");
gt(fa, "onTransitionEnd");
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
var Nn = "abort canplay canplaythrough durationchange emptied encrypted ended error loadeddata loadedmetadata loadstart pause play playing progress ratechange resize seeked seeking stalled suspend timeupdate volumechange waiting".split(" "), cd = new Set("cancel close invalid load scroll toggle".split(" ").concat(Nn));
function yu(e, t, n) {
  var r = e.type || "unknown-event";
  e.currentTarget = n, sf(r, t, void 0, e), e.currentTarget = null;
}
function pa(e, t) {
  t = (t & 4) !== 0;
  for (var n = 0; n < e.length; n++) {
    var r = e[n], l = r.event;
    r = r.listeners;
    e: {
      var o = void 0;
      if (t) for (var i = r.length - 1; 0 <= i; i--) {
        var u = r[i], s = u.instance, c = u.currentTarget;
        if (u = u.listener, s !== o && l.isPropagationStopped()) break e;
        yu(l, u, c), o = s;
      }
      else for (i = 0; i < r.length; i++) {
        if (u = r[i], s = u.instance, c = u.currentTarget, u = u.listener, s !== o && l.isPropagationStopped()) break e;
        yu(l, u, c), o = s;
      }
    }
  }
  if (Ar) throw e = go, Ar = !1, go = null, e;
}
function V(e, t) {
  var n = t[Po];
  n === void 0 && (n = t[Po] = /* @__PURE__ */ new Set());
  var r = e + "__bubble";
  n.has(r) || (ma(t, e, 2, !1), n.add(r));
}
function Al(e, t, n) {
  var r = 0;
  t && (r |= 4), ma(n, e, r, t);
}
var gr = "_reactListening" + Math.random().toString(36).slice(2);
function Qn(e) {
  if (!e[gr]) {
    e[gr] = !0, Ss.forEach(function(n) {
      n !== "selectionchange" && (cd.has(n) || Al(n, !1, e), Al(n, !0, e));
    });
    var t = e.nodeType === 9 ? e : e.ownerDocument;
    t === null || t[gr] || (t[gr] = !0, Al("selectionchange", !1, t));
  }
}
function ma(e, t, n, r) {
  switch (Js(t)) {
    case 1:
      var l = Cf;
      break;
    case 4:
      l = _f;
      break;
    default:
      l = si;
  }
  n = l.bind(null, t, n, e), l = void 0, !vo || t !== "touchstart" && t !== "touchmove" && t !== "wheel" || (l = !0), r ? l !== void 0 ? e.addEventListener(t, n, { capture: !0, passive: l }) : e.addEventListener(t, n, !0) : l !== void 0 ? e.addEventListener(t, n, { passive: l }) : e.addEventListener(t, n, !1);
}
function Vl(e, t, n, r, l) {
  var o = r;
  if (!(t & 1) && !(t & 2) && r !== null) e: for (; ; ) {
    if (r === null) return;
    var i = r.tag;
    if (i === 3 || i === 4) {
      var u = r.stateNode.containerInfo;
      if (u === l || u.nodeType === 8 && u.parentNode === l) break;
      if (i === 4) for (i = r.return; i !== null; ) {
        var s = i.tag;
        if ((s === 3 || s === 4) && (s = i.stateNode.containerInfo, s === l || s.nodeType === 8 && s.parentNode === l)) return;
        i = i.return;
      }
      for (; u !== null; ) {
        if (i = Ct(u), i === null) return;
        if (s = i.tag, s === 5 || s === 6) {
          r = o = i;
          continue e;
        }
        u = u.parentNode;
      }
    }
    r = r.return;
  }
  Fs(function() {
    var c = o, g = li(n), h = [];
    e: {
      var f = da.get(e);
      if (f !== void 0) {
        var w = ci, k = e;
        switch (e) {
          case "keypress":
            if (Lr(n) === 0) break e;
          case "keydown":
          case "keyup":
            w = Vf;
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
            w = jf;
            break;
          case "touchcancel":
          case "touchend":
          case "touchmove":
          case "touchstart":
            w = Hf;
            break;
          case sa:
          case aa:
          case ca:
            w = zf;
            break;
          case fa:
            w = Wf;
            break;
          case "scroll":
            w = Nf;
            break;
          case "wheel":
            w = Yf;
            break;
          case "copy":
          case "cut":
          case "paste":
            w = Mf;
            break;
          case "gotpointercapture":
          case "lostpointercapture":
          case "pointercancel":
          case "pointerdown":
          case "pointermove":
          case "pointerout":
          case "pointerover":
          case "pointerup":
            w = su;
        }
        var E = (t & 4) !== 0, z = !E && e === "scroll", d = E ? f !== null ? f + "Capture" : null : f;
        E = [];
        for (var a = c, p; a !== null; ) {
          p = a;
          var v = p.stateNode;
          if (p.tag === 5 && v !== null && (p = v, d !== null && (v = Un(a, d), v != null && E.push(Wn(a, v, p)))), z) break;
          a = a.return;
        }
        0 < E.length && (f = new w(f, k, null, n, g), h.push({ event: f, listeners: E }));
      }
    }
    if (!(t & 7)) {
      e: {
        if (f = e === "mouseover" || e === "pointerover", w = e === "mouseout" || e === "pointerout", f && n !== mo && (k = n.relatedTarget || n.fromElement) && (Ct(k) || k[Ze])) break e;
        if ((w || f) && (f = g.window === g ? g : (f = g.ownerDocument) ? f.defaultView || f.parentWindow : window, w ? (k = n.relatedTarget || n.toElement, w = c, k = k ? Ct(k) : null, k !== null && (z = Dt(k), k !== z || k.tag !== 5 && k.tag !== 6) && (k = null)) : (w = null, k = c), w !== k)) {
          if (E = iu, v = "onMouseLeave", d = "onMouseEnter", a = "mouse", (e === "pointerout" || e === "pointerover") && (E = su, v = "onPointerLeave", d = "onPointerEnter", a = "pointer"), z = w == null ? f : Bt(w), p = k == null ? f : Bt(k), f = new E(v, a + "leave", w, n, g), f.target = z, f.relatedTarget = p, v = null, Ct(g) === c && (E = new E(d, a + "enter", k, n, g), E.target = p, E.relatedTarget = z, v = E), z = v, w && k) t: {
            for (E = w, d = k, a = 0, p = E; p; p = Ot(p)) a++;
            for (p = 0, v = d; v; v = Ot(v)) p++;
            for (; 0 < a - p; ) E = Ot(E), a--;
            for (; 0 < p - a; ) d = Ot(d), p--;
            for (; a--; ) {
              if (E === d || d !== null && E === d.alternate) break t;
              E = Ot(E), d = Ot(d);
            }
            E = null;
          }
          else E = null;
          w !== null && wu(h, f, w, E, !1), k !== null && z !== null && wu(h, z, k, E, !0);
        }
      }
      e: {
        if (f = c ? Bt(c) : window, w = f.nodeName && f.nodeName.toLowerCase(), w === "select" || w === "input" && f.type === "file") var C = ed;
        else if (fu(f)) if (ra) C = ld;
        else {
          C = nd;
          var _ = td;
        }
        else (w = f.nodeName) && w.toLowerCase() === "input" && (f.type === "checkbox" || f.type === "radio") && (C = rd);
        if (C && (C = C(e, c))) {
          na(h, C, n, g);
          break e;
        }
        _ && _(e, f, c), e === "focusout" && (_ = f._wrapperState) && _.controlled && f.type === "number" && so(f, "number", f.value);
      }
      switch (_ = c ? Bt(c) : window, e) {
        case "focusin":
          (fu(_) || _.contentEditable === "true") && (Vt = _, So = c, zn = null);
          break;
        case "focusout":
          zn = So = Vt = null;
          break;
        case "mousedown":
          xo = !0;
          break;
        case "contextmenu":
        case "mouseup":
        case "dragend":
          xo = !1, vu(h, n, g);
          break;
        case "selectionchange":
          if (ud) break;
        case "keydown":
        case "keyup":
          vu(h, n, g);
      }
      var x;
      if (di) e: {
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
      else At ? ea(e, n) && (N = "onCompositionEnd") : e === "keydown" && n.keyCode === 229 && (N = "onCompositionStart");
      N && (bs && n.locale !== "ko" && (At || N !== "onCompositionStart" ? N === "onCompositionEnd" && At && (x = qs()) : (ot = g, ai = "value" in ot ? ot.value : ot.textContent, At = !0)), _ = Qr(c, N), 0 < _.length && (N = new uu(N, e, null, n, g), h.push({ event: N, listeners: _ }), x ? N.data = x : (x = ta(n), x !== null && (N.data = x)))), (x = Gf ? Zf(e, n) : Jf(e, n)) && (c = Qr(c, "onBeforeInput"), 0 < c.length && (g = new uu("onBeforeInput", "beforeinput", null, n, g), h.push({ event: g, listeners: c }), g.data = x));
    }
    pa(h, t);
  });
}
function Wn(e, t, n) {
  return { instance: e, listener: t, currentTarget: n };
}
function Qr(e, t) {
  for (var n = t + "Capture", r = []; e !== null; ) {
    var l = e, o = l.stateNode;
    l.tag === 5 && o !== null && (l = o, o = Un(e, n), o != null && r.unshift(Wn(e, o, l)), o = Un(e, t), o != null && r.push(Wn(e, o, l))), e = e.return;
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
function wu(e, t, n, r, l) {
  for (var o = t._reactName, i = []; n !== null && n !== r; ) {
    var u = n, s = u.alternate, c = u.stateNode;
    if (s !== null && s === r) break;
    u.tag === 5 && c !== null && (u = c, l ? (s = Un(n, o), s != null && i.unshift(Wn(n, s, u))) : l || (s = Un(n, o), s != null && i.push(Wn(n, s, u)))), n = n.return;
  }
  i.length !== 0 && e.push({ event: t, listeners: i });
}
var fd = /\r\n?/g, dd = /\u0000|\uFFFD/g;
function ku(e) {
  return (typeof e == "string" ? e : "" + e).replace(fd, `
`).replace(dd, "");
}
function yr(e, t, n) {
  if (t = ku(t), ku(e) !== t && n) throw Error(S(425));
}
function Wr() {
}
var Eo = null, Co = null;
function _o(e, t) {
  return e === "textarea" || e === "noscript" || typeof t.children == "string" || typeof t.children == "number" || typeof t.dangerouslySetInnerHTML == "object" && t.dangerouslySetInnerHTML !== null && t.dangerouslySetInnerHTML.__html != null;
}
var No = typeof setTimeout == "function" ? setTimeout : void 0, pd = typeof clearTimeout == "function" ? clearTimeout : void 0, Su = typeof Promise == "function" ? Promise : void 0, md = typeof queueMicrotask == "function" ? queueMicrotask : typeof Su < "u" ? function(e) {
  return Su.resolve(null).then(e).catch(hd);
} : No;
function hd(e) {
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
function xu(e) {
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
var fn = Math.random().toString(36).slice(2), $e = "__reactFiber$" + fn, Kn = "__reactProps$" + fn, Ze = "__reactContainer$" + fn, Po = "__reactEvents$" + fn, vd = "__reactListeners$" + fn, gd = "__reactHandles$" + fn;
function Ct(e) {
  var t = e[$e];
  if (t) return t;
  for (var n = e.parentNode; n; ) {
    if (t = n[Ze] || n[$e]) {
      if (n = t.alternate, t.child !== null || n !== null && n.child !== null) for (e = xu(e); e !== null; ) {
        if (n = e[$e]) return n;
        e = xu(e);
      }
      return t;
    }
    e = n, n = e.parentNode;
  }
  return null;
}
function rr(e) {
  return e = e[$e] || e[Ze], !e || e.tag !== 5 && e.tag !== 6 && e.tag !== 13 && e.tag !== 3 ? null : e;
}
function Bt(e) {
  if (e.tag === 5 || e.tag === 6) return e.stateNode;
  throw Error(S(33));
}
function dl(e) {
  return e[Kn] || null;
}
var jo = [], Ht = -1;
function yt(e) {
  return { current: e };
}
function $(e) {
  0 > Ht || (e.current = jo[Ht], jo[Ht] = null, Ht--);
}
function U(e, t) {
  Ht++, jo[Ht] = e.current, e.current = t;
}
var vt = {}, se = yt(vt), ve = yt(!1), Lt = vt;
function nn(e, t) {
  var n = e.type.contextTypes;
  if (!n) return vt;
  var r = e.stateNode;
  if (r && r.__reactInternalMemoizedUnmaskedChildContext === t) return r.__reactInternalMemoizedMaskedChildContext;
  var l = {}, o;
  for (o in n) l[o] = t[o];
  return r && (e = e.stateNode, e.__reactInternalMemoizedUnmaskedChildContext = t, e.__reactInternalMemoizedMaskedChildContext = l), l;
}
function ge(e) {
  return e = e.childContextTypes, e != null;
}
function Kr() {
  $(ve), $(se);
}
function Eu(e, t, n) {
  if (se.current !== vt) throw Error(S(168));
  U(se, t), U(ve, n);
}
function ha(e, t, n) {
  var r = e.stateNode;
  if (t = t.childContextTypes, typeof r.getChildContext != "function") return n;
  r = r.getChildContext();
  for (var l in r) if (!(l in t)) throw Error(S(108, ef(e) || "Unknown", l));
  return K({}, n, r);
}
function Yr(e) {
  return e = (e = e.stateNode) && e.__reactInternalMemoizedMergedChildContext || vt, Lt = se.current, U(se, e), U(ve, ve.current), !0;
}
function Cu(e, t, n) {
  var r = e.stateNode;
  if (!r) throw Error(S(169));
  n ? (e = ha(e, t, Lt), r.__reactInternalMemoizedMergedChildContext = e, $(ve), $(se), U(se, e)) : $(ve), U(ve, n);
}
var We = null, pl = !1, Bl = !1;
function va(e) {
  We === null ? We = [e] : We.push(e);
}
function yd(e) {
  pl = !0, va(e);
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
      throw We !== null && (We = We.slice(e + 1)), $s(oi, wt), l;
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
function ga(e, t, n) {
  Ne[Pe++] = Ke, Ne[Pe++] = Ye, Ne[Pe++] = Tt, Tt = e;
  var r = Ke;
  e = Ye;
  var l = 32 - Oe(r) - 1;
  r &= ~(1 << l), n += 1;
  var o = 32 - Oe(t) + l;
  if (30 < o) {
    var i = l - l % 5;
    o = (r & (1 << i) - 1).toString(32), r >>= i, l -= i, Ke = 1 << 32 - Oe(t) + l | n << l | r, Ye = o + e;
  } else Ke = 1 << o | n << l | r, Ye = e;
}
function mi(e) {
  e.return !== null && (xt(e, 1), ga(e, 1, 0));
}
function hi(e) {
  for (; e === Xr; ) Xr = Qt[--Wt], Qt[Wt] = null, Gr = Qt[--Wt], Qt[Wt] = null;
  for (; e === Tt; ) Tt = Ne[--Pe], Ne[Pe] = null, Ye = Ne[--Pe], Ne[Pe] = null, Ke = Ne[--Pe], Ne[Pe] = null;
}
var Se = null, ke = null, B = !1, De = null;
function ya(e, t) {
  var n = je(5, null, null, 0);
  n.elementType = "DELETED", n.stateNode = t, n.return = e, t = e.deletions, t === null ? (e.deletions = [n], e.flags |= 16) : t.push(n);
}
function _u(e, t) {
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
function Lo(e) {
  return (e.mode & 1) !== 0 && (e.flags & 128) === 0;
}
function To(e) {
  if (B) {
    var t = ke;
    if (t) {
      var n = t;
      if (!_u(e, t)) {
        if (Lo(e)) throw Error(S(418));
        t = ct(n.nextSibling);
        var r = Se;
        t && _u(e, t) ? ya(r, n) : (e.flags = e.flags & -4097 | 2, B = !1, Se = e);
      }
    } else {
      if (Lo(e)) throw Error(S(418));
      e.flags = e.flags & -4097 | 2, B = !1, Se = e;
    }
  }
}
function Nu(e) {
  for (e = e.return; e !== null && e.tag !== 5 && e.tag !== 3 && e.tag !== 13; ) e = e.return;
  Se = e;
}
function wr(e) {
  if (e !== Se) return !1;
  if (!B) return Nu(e), B = !0, !1;
  var t;
  if ((t = e.tag !== 3) && !(t = e.tag !== 5) && (t = e.type, t = t !== "head" && t !== "body" && !_o(e.type, e.memoizedProps)), t && (t = ke)) {
    if (Lo(e)) throw wa(), Error(S(418));
    for (; t; ) ya(e, t), t = ct(t.nextSibling);
  }
  if (Nu(e), e.tag === 13) {
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
function wa() {
  for (var e = ke; e; ) e = ct(e.nextSibling);
}
function rn() {
  ke = Se = null, B = !1;
}
function vi(e) {
  De === null ? De = [e] : De.push(e);
}
var wd = be.ReactCurrentBatchConfig;
function kn(e, t, n) {
  if (e = n.ref, e !== null && typeof e != "function" && typeof e != "object") {
    if (n._owner) {
      if (n = n._owner, n) {
        if (n.tag !== 1) throw Error(S(309));
        var r = n.stateNode;
      }
      if (!r) throw Error(S(147, e));
      var l = r, o = "" + e;
      return t !== null && t.ref !== null && typeof t.ref == "function" && t.ref._stringRef === o ? t.ref : (t = function(i) {
        var u = l.refs;
        i === null ? delete u[o] : u[o] = i;
      }, t._stringRef = o, t);
    }
    if (typeof e != "string") throw Error(S(284));
    if (!n._owner) throw Error(S(290, e));
  }
  return e;
}
function kr(e, t) {
  throw e = Object.prototype.toString.call(t), Error(S(31, e === "[object Object]" ? "object with keys {" + Object.keys(t).join(", ") + "}" : e));
}
function Pu(e) {
  var t = e._init;
  return t(e._payload);
}
function ka(e) {
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
  function o(d, a, p) {
    return d.index = p, e ? (p = d.alternate, p !== null ? (p = p.index, p < a ? (d.flags |= 2, a) : p) : (d.flags |= 2, a)) : (d.flags |= 1048576, a);
  }
  function i(d) {
    return e && d.alternate === null && (d.flags |= 2), d;
  }
  function u(d, a, p, v) {
    return a === null || a.tag !== 6 ? (a = Gl(p, d.mode, v), a.return = d, a) : (a = l(a, p), a.return = d, a);
  }
  function s(d, a, p, v) {
    var C = p.type;
    return C === Ut ? g(d, a, p.props.children, v, p.key) : a !== null && (a.elementType === C || typeof C == "object" && C !== null && C.$$typeof === tt && Pu(C) === a.type) ? (v = l(a, p.props), v.ref = kn(d, a, p), v.return = d, v) : (v = Or(p.type, p.key, p.props, null, d.mode, v), v.ref = kn(d, a, p), v.return = d, v);
  }
  function c(d, a, p, v) {
    return a === null || a.tag !== 4 || a.stateNode.containerInfo !== p.containerInfo || a.stateNode.implementation !== p.implementation ? (a = Zl(p, d.mode, v), a.return = d, a) : (a = l(a, p.children || []), a.return = d, a);
  }
  function g(d, a, p, v, C) {
    return a === null || a.tag !== 7 ? (a = jt(p, d.mode, v, C), a.return = d, a) : (a = l(a, p), a.return = d, a);
  }
  function h(d, a, p) {
    if (typeof a == "string" && a !== "" || typeof a == "number") return a = Gl("" + a, d.mode, p), a.return = d, a;
    if (typeof a == "object" && a !== null) {
      switch (a.$$typeof) {
        case ar:
          return p = Or(a.type, a.key, a.props, null, d.mode, p), p.ref = kn(d, null, a), p.return = d, p;
        case Ft:
          return a = Zl(a, d.mode, p), a.return = d, a;
        case tt:
          var v = a._init;
          return h(d, v(a._payload), p);
      }
      if (Cn(a) || hn(a)) return a = jt(a, d.mode, p, null), a.return = d, a;
      kr(d, a);
    }
    return null;
  }
  function f(d, a, p, v) {
    var C = a !== null ? a.key : null;
    if (typeof p == "string" && p !== "" || typeof p == "number") return C !== null ? null : u(d, a, "" + p, v);
    if (typeof p == "object" && p !== null) {
      switch (p.$$typeof) {
        case ar:
          return p.key === C ? s(d, a, p, v) : null;
        case Ft:
          return p.key === C ? c(d, a, p, v) : null;
        case tt:
          return C = p._init, f(
            d,
            a,
            C(p._payload),
            v
          );
      }
      if (Cn(p) || hn(p)) return C !== null ? null : g(d, a, p, v, null);
      kr(d, p);
    }
    return null;
  }
  function w(d, a, p, v, C) {
    if (typeof v == "string" && v !== "" || typeof v == "number") return d = d.get(p) || null, u(a, d, "" + v, C);
    if (typeof v == "object" && v !== null) {
      switch (v.$$typeof) {
        case ar:
          return d = d.get(v.key === null ? p : v.key) || null, s(a, d, v, C);
        case Ft:
          return d = d.get(v.key === null ? p : v.key) || null, c(a, d, v, C);
        case tt:
          var _ = v._init;
          return w(d, a, p, _(v._payload), C);
      }
      if (Cn(v) || hn(v)) return d = d.get(p) || null, g(a, d, v, C, null);
      kr(a, v);
    }
    return null;
  }
  function k(d, a, p, v) {
    for (var C = null, _ = null, x = a, N = a = 0, I = null; x !== null && N < p.length; N++) {
      x.index > N ? (I = x, x = null) : I = x.sibling;
      var y = f(d, x, p[N], v);
      if (y === null) {
        x === null && (x = I);
        break;
      }
      e && x && y.alternate === null && t(d, x), a = o(y, a, N), _ === null ? C = y : _.sibling = y, _ = y, x = I;
    }
    if (N === p.length) return n(d, x), B && xt(d, N), C;
    if (x === null) {
      for (; N < p.length; N++) x = h(d, p[N], v), x !== null && (a = o(x, a, N), _ === null ? C = x : _.sibling = x, _ = x);
      return B && xt(d, N), C;
    }
    for (x = r(d, x); N < p.length; N++) I = w(x, d, N, p[N], v), I !== null && (e && I.alternate !== null && x.delete(I.key === null ? N : I.key), a = o(I, a, N), _ === null ? C = I : _.sibling = I, _ = I);
    return e && x.forEach(function(P) {
      return t(d, P);
    }), B && xt(d, N), C;
  }
  function E(d, a, p, v) {
    var C = hn(p);
    if (typeof C != "function") throw Error(S(150));
    if (p = C.call(p), p == null) throw Error(S(151));
    for (var _ = C = null, x = a, N = a = 0, I = null, y = p.next(); x !== null && !y.done; N++, y = p.next()) {
      x.index > N ? (I = x, x = null) : I = x.sibling;
      var P = f(d, x, y.value, v);
      if (P === null) {
        x === null && (x = I);
        break;
      }
      e && x && P.alternate === null && t(d, x), a = o(P, a, N), _ === null ? C = P : _.sibling = P, _ = P, x = I;
    }
    if (y.done) return n(
      d,
      x
    ), B && xt(d, N), C;
    if (x === null) {
      for (; !y.done; N++, y = p.next()) y = h(d, y.value, v), y !== null && (a = o(y, a, N), _ === null ? C = y : _.sibling = y, _ = y);
      return B && xt(d, N), C;
    }
    for (x = r(d, x); !y.done; N++, y = p.next()) y = w(x, d, N, y.value, v), y !== null && (e && y.alternate !== null && x.delete(y.key === null ? N : y.key), a = o(y, a, N), _ === null ? C = y : _.sibling = y, _ = y);
    return e && x.forEach(function(A) {
      return t(d, A);
    }), B && xt(d, N), C;
  }
  function z(d, a, p, v) {
    if (typeof p == "object" && p !== null && p.type === Ut && p.key === null && (p = p.props.children), typeof p == "object" && p !== null) {
      switch (p.$$typeof) {
        case ar:
          e: {
            for (var C = p.key, _ = a; _ !== null; ) {
              if (_.key === C) {
                if (C = p.type, C === Ut) {
                  if (_.tag === 7) {
                    n(d, _.sibling), a = l(_, p.props.children), a.return = d, d = a;
                    break e;
                  }
                } else if (_.elementType === C || typeof C == "object" && C !== null && C.$$typeof === tt && Pu(C) === _.type) {
                  n(d, _.sibling), a = l(_, p.props), a.ref = kn(d, _, p), a.return = d, d = a;
                  break e;
                }
                n(d, _);
                break;
              } else t(d, _);
              _ = _.sibling;
            }
            p.type === Ut ? (a = jt(p.props.children, d.mode, v, p.key), a.return = d, d = a) : (v = Or(p.type, p.key, p.props, null, d.mode, v), v.ref = kn(d, a, p), v.return = d, d = v);
          }
          return i(d);
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
            a = Zl(p, d.mode, v), a.return = d, d = a;
          }
          return i(d);
        case tt:
          return _ = p._init, z(d, a, _(p._payload), v);
      }
      if (Cn(p)) return k(d, a, p, v);
      if (hn(p)) return E(d, a, p, v);
      kr(d, p);
    }
    return typeof p == "string" && p !== "" || typeof p == "number" ? (p = "" + p, a !== null && a.tag === 6 ? (n(d, a.sibling), a = l(a, p), a.return = d, d = a) : (n(d, a), a = Gl(p, d.mode, v), a.return = d, d = a), i(d)) : n(d, a);
  }
  return z;
}
var ln = ka(!0), Sa = ka(!1), Zr = yt(null), Jr = null, Kt = null, gi = null;
function yi() {
  gi = Kt = Jr = null;
}
function wi(e) {
  var t = Zr.current;
  $(Zr), e._currentValue = t;
}
function zo(e, t, n) {
  for (; e !== null; ) {
    var r = e.alternate;
    if ((e.childLanes & t) !== t ? (e.childLanes |= t, r !== null && (r.childLanes |= t)) : r !== null && (r.childLanes & t) !== t && (r.childLanes |= t), e === n) break;
    e = e.return;
  }
}
function bt(e, t) {
  Jr = e, gi = Kt = null, e = e.dependencies, e !== null && e.firstContext !== null && (e.lanes & t && (he = !0), e.firstContext = null);
}
function Te(e) {
  var t = e._currentValue;
  if (gi !== e) if (e = { context: e, memoizedValue: t, next: null }, Kt === null) {
    if (Jr === null) throw Error(S(308));
    Kt = e, Jr.dependencies = { lanes: 0, firstContext: e };
  } else Kt = Kt.next = e;
  return t;
}
var _t = null;
function ki(e) {
  _t === null ? _t = [e] : _t.push(e);
}
function xa(e, t, n, r) {
  var l = t.interleaved;
  return l === null ? (n.next = n, ki(t)) : (n.next = l.next, l.next = n), t.interleaved = n, Je(e, r);
}
function Je(e, t) {
  e.lanes |= t;
  var n = e.alternate;
  for (n !== null && (n.lanes |= t), n = e, e = e.return; e !== null; ) e.childLanes |= t, n = e.alternate, n !== null && (n.childLanes |= t), n = e, e = e.return;
  return n.tag === 3 ? n.stateNode : null;
}
var nt = !1;
function Si(e) {
  e.updateQueue = { baseState: e.memoizedState, firstBaseUpdate: null, lastBaseUpdate: null, shared: { pending: null, interleaved: null, lanes: 0 }, effects: null };
}
function Ea(e, t) {
  e = e.updateQueue, t.updateQueue === e && (t.updateQueue = { baseState: e.baseState, firstBaseUpdate: e.firstBaseUpdate, lastBaseUpdate: e.lastBaseUpdate, shared: e.shared, effects: e.effects });
}
function Xe(e, t) {
  return { eventTime: e, lane: t, tag: 0, payload: null, callback: null, next: null };
}
function ft(e, t, n) {
  var r = e.updateQueue;
  if (r === null) return null;
  if (r = r.shared, D & 2) {
    var l = r.pending;
    return l === null ? t.next = t : (t.next = l.next, l.next = t), r.pending = t, Je(e, n);
  }
  return l = r.interleaved, l === null ? (t.next = t, ki(r)) : (t.next = l.next, l.next = t), r.interleaved = t, Je(e, n);
}
function Tr(e, t, n) {
  if (t = t.updateQueue, t !== null && (t = t.shared, (n & 4194240) !== 0)) {
    var r = t.lanes;
    r &= e.pendingLanes, n |= r, t.lanes = n, ii(e, n);
  }
}
function ju(e, t) {
  var n = e.updateQueue, r = e.alternate;
  if (r !== null && (r = r.updateQueue, n === r)) {
    var l = null, o = null;
    if (n = n.firstBaseUpdate, n !== null) {
      do {
        var i = { eventTime: n.eventTime, lane: n.lane, tag: n.tag, payload: n.payload, callback: n.callback, next: null };
        o === null ? l = o = i : o = o.next = i, n = n.next;
      } while (n !== null);
      o === null ? l = o = t : o = o.next = t;
    } else l = o = t;
    n = { baseState: r.baseState, firstBaseUpdate: l, lastBaseUpdate: o, shared: r.shared, effects: r.effects }, e.updateQueue = n;
    return;
  }
  e = n.lastBaseUpdate, e === null ? n.firstBaseUpdate = t : e.next = t, n.lastBaseUpdate = t;
}
function qr(e, t, n, r) {
  var l = e.updateQueue;
  nt = !1;
  var o = l.firstBaseUpdate, i = l.lastBaseUpdate, u = l.shared.pending;
  if (u !== null) {
    l.shared.pending = null;
    var s = u, c = s.next;
    s.next = null, i === null ? o = c : i.next = c, i = s;
    var g = e.alternate;
    g !== null && (g = g.updateQueue, u = g.lastBaseUpdate, u !== i && (u === null ? g.firstBaseUpdate = c : u.next = c, g.lastBaseUpdate = s));
  }
  if (o !== null) {
    var h = l.baseState;
    i = 0, g = c = s = null, u = o;
    do {
      var f = u.lane, w = u.eventTime;
      if ((r & f) === f) {
        g !== null && (g = g.next = {
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
                h = k.call(w, h, f);
                break e;
              }
              h = k;
              break e;
            case 3:
              k.flags = k.flags & -65537 | 128;
            case 0:
              if (k = E.payload, f = typeof k == "function" ? k.call(w, h, f) : k, f == null) break e;
              h = K({}, h, f);
              break e;
            case 2:
              nt = !0;
          }
        }
        u.callback !== null && u.lane !== 0 && (e.flags |= 64, f = l.effects, f === null ? l.effects = [u] : f.push(u));
      } else w = { eventTime: w, lane: f, tag: u.tag, payload: u.payload, callback: u.callback, next: null }, g === null ? (c = g = w, s = h) : g = g.next = w, i |= f;
      if (u = u.next, u === null) {
        if (u = l.shared.pending, u === null) break;
        f = u, u = f.next, f.next = null, l.lastBaseUpdate = f, l.shared.pending = null;
      }
    } while (!0);
    if (g === null && (s = h), l.baseState = s, l.firstBaseUpdate = c, l.lastBaseUpdate = g, t = l.shared.interleaved, t !== null) {
      l = t;
      do
        i |= l.lane, l = l.next;
      while (l !== t);
    } else o === null && (l.shared.lanes = 0);
    Rt |= i, e.lanes = i, e.memoizedState = h;
  }
}
function Lu(e, t, n) {
  if (e = t.effects, t.effects = null, e !== null) for (t = 0; t < e.length; t++) {
    var r = e[t], l = r.callback;
    if (l !== null) {
      if (r.callback = null, r = n, typeof l != "function") throw Error(S(191, l));
      l.call(r);
    }
  }
}
var lr = {}, He = yt(lr), Yn = yt(lr), Xn = yt(lr);
function Nt(e) {
  if (e === lr) throw Error(S(174));
  return e;
}
function xi(e, t) {
  switch (U(Xn, t), U(Yn, e), U(He, lr), e = t.nodeType, e) {
    case 9:
    case 11:
      t = (t = t.documentElement) ? t.namespaceURI : co(null, "");
      break;
    default:
      e = e === 8 ? t.parentNode : t, t = e.namespaceURI || null, e = e.tagName, t = co(t, e);
  }
  $(He), U(He, t);
}
function on() {
  $(He), $(Yn), $(Xn);
}
function Ca(e) {
  Nt(Xn.current);
  var t = Nt(He.current), n = co(t, e.type);
  t !== n && (U(Yn, e), U(He, n));
}
function Ei(e) {
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
function Ci() {
  for (var e = 0; e < Hl.length; e++) Hl[e]._workInProgressVersionPrimary = null;
  Hl.length = 0;
}
var zr = be.ReactCurrentDispatcher, Ql = be.ReactCurrentBatchConfig, zt = 0, W = null, J = null, ee = null, el = !1, Rn = !1, Gn = 0, kd = 0;
function oe() {
  throw Error(S(321));
}
function _i(e, t) {
  if (t === null) return !1;
  for (var n = 0; n < t.length && n < e.length; n++) if (!Ue(e[n], t[n])) return !1;
  return !0;
}
function Ni(e, t, n, r, l, o) {
  if (zt = o, W = t, t.memoizedState = null, t.updateQueue = null, t.lanes = 0, zr.current = e === null || e.memoizedState === null ? Cd : _d, e = n(r, l), Rn) {
    o = 0;
    do {
      if (Rn = !1, Gn = 0, 25 <= o) throw Error(S(301));
      o += 1, ee = J = null, t.updateQueue = null, zr.current = Nd, e = n(r, l);
    } while (Rn);
  }
  if (zr.current = tl, t = J !== null && J.next !== null, zt = 0, ee = J = W = null, el = !1, t) throw Error(S(300));
  return e;
}
function Pi() {
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
  var r = J, l = r.baseQueue, o = n.pending;
  if (o !== null) {
    if (l !== null) {
      var i = l.next;
      l.next = o.next, o.next = i;
    }
    r.baseQueue = l = o, n.pending = null;
  }
  if (l !== null) {
    o = l.next, r = r.baseState;
    var u = i = null, s = null, c = o;
    do {
      var g = c.lane;
      if ((zt & g) === g) s !== null && (s = s.next = { lane: 0, action: c.action, hasEagerState: c.hasEagerState, eagerState: c.eagerState, next: null }), r = c.hasEagerState ? c.eagerState : e(r, c.action);
      else {
        var h = {
          lane: g,
          action: c.action,
          hasEagerState: c.hasEagerState,
          eagerState: c.eagerState,
          next: null
        };
        s === null ? (u = s = h, i = r) : s = s.next = h, W.lanes |= g, Rt |= g;
      }
      c = c.next;
    } while (c !== null && c !== o);
    s === null ? i = r : s.next = u, Ue(r, t.memoizedState) || (he = !0), t.memoizedState = r, t.baseState = i, t.baseQueue = s, n.lastRenderedState = r;
  }
  if (e = n.interleaved, e !== null) {
    l = e;
    do
      o = l.lane, W.lanes |= o, Rt |= o, l = l.next;
    while (l !== e);
  } else l === null && (n.lanes = 0);
  return [t.memoizedState, n.dispatch];
}
function Kl(e) {
  var t = ze(), n = t.queue;
  if (n === null) throw Error(S(311));
  n.lastRenderedReducer = e;
  var r = n.dispatch, l = n.pending, o = t.memoizedState;
  if (l !== null) {
    n.pending = null;
    var i = l = l.next;
    do
      o = e(o, i.action), i = i.next;
    while (i !== l);
    Ue(o, t.memoizedState) || (he = !0), t.memoizedState = o, t.baseQueue === null && (t.baseState = o), n.lastRenderedState = o;
  }
  return [o, r];
}
function _a() {
}
function Na(e, t) {
  var n = W, r = ze(), l = t(), o = !Ue(r.memoizedState, l);
  if (o && (r.memoizedState = l, he = !0), r = r.queue, ji(La.bind(null, n, r, e), [e]), r.getSnapshot !== t || o || ee !== null && ee.memoizedState.tag & 1) {
    if (n.flags |= 2048, Jn(9, ja.bind(null, n, r, l, t), void 0, null), te === null) throw Error(S(349));
    zt & 30 || Pa(n, t, l);
  }
  return l;
}
function Pa(e, t, n) {
  e.flags |= 16384, e = { getSnapshot: t, value: n }, t = W.updateQueue, t === null ? (t = { lastEffect: null, stores: null }, W.updateQueue = t, t.stores = [e]) : (n = t.stores, n === null ? t.stores = [e] : n.push(e));
}
function ja(e, t, n, r) {
  t.value = n, t.getSnapshot = r, Ta(t) && za(e);
}
function La(e, t, n) {
  return n(function() {
    Ta(t) && za(e);
  });
}
function Ta(e) {
  var t = e.getSnapshot;
  e = e.value;
  try {
    var n = t();
    return !Ue(e, n);
  } catch {
    return !0;
  }
}
function za(e) {
  var t = Je(e, 1);
  t !== null && Fe(t, e, 1, -1);
}
function Tu(e) {
  var t = Ve();
  return typeof e == "function" && (e = e()), t.memoizedState = t.baseState = e, e = { pending: null, interleaved: null, lanes: 0, dispatch: null, lastRenderedReducer: Zn, lastRenderedState: e }, t.queue = e, e = e.dispatch = Ed.bind(null, W, e), [t.memoizedState, e];
}
function Jn(e, t, n, r) {
  return e = { tag: e, create: t, destroy: n, deps: r, next: null }, t = W.updateQueue, t === null ? (t = { lastEffect: null, stores: null }, W.updateQueue = t, t.lastEffect = e.next = e) : (n = t.lastEffect, n === null ? t.lastEffect = e.next = e : (r = n.next, n.next = e, e.next = r, t.lastEffect = e)), e;
}
function Ra() {
  return ze().memoizedState;
}
function Rr(e, t, n, r) {
  var l = Ve();
  W.flags |= e, l.memoizedState = Jn(1 | t, n, void 0, r === void 0 ? null : r);
}
function ml(e, t, n, r) {
  var l = ze();
  r = r === void 0 ? null : r;
  var o = void 0;
  if (J !== null) {
    var i = J.memoizedState;
    if (o = i.destroy, r !== null && _i(r, i.deps)) {
      l.memoizedState = Jn(t, n, o, r);
      return;
    }
  }
  W.flags |= e, l.memoizedState = Jn(1 | t, n, o, r);
}
function zu(e, t) {
  return Rr(8390656, 8, e, t);
}
function ji(e, t) {
  return ml(2048, 8, e, t);
}
function Ma(e, t) {
  return ml(4, 2, e, t);
}
function Ia(e, t) {
  return ml(4, 4, e, t);
}
function Da(e, t) {
  if (typeof t == "function") return e = e(), t(e), function() {
    t(null);
  };
  if (t != null) return e = e(), t.current = e, function() {
    t.current = null;
  };
}
function Oa(e, t, n) {
  return n = n != null ? n.concat([e]) : null, ml(4, 4, Da.bind(null, t, e), n);
}
function Li() {
}
function Fa(e, t) {
  var n = ze();
  t = t === void 0 ? null : t;
  var r = n.memoizedState;
  return r !== null && t !== null && _i(t, r[1]) ? r[0] : (n.memoizedState = [e, t], e);
}
function Ua(e, t) {
  var n = ze();
  t = t === void 0 ? null : t;
  var r = n.memoizedState;
  return r !== null && t !== null && _i(t, r[1]) ? r[0] : (e = e(), n.memoizedState = [e, t], e);
}
function Aa(e, t, n) {
  return zt & 21 ? (Ue(n, t) || (n = Qs(), W.lanes |= n, Rt |= n, e.baseState = !0), t) : (e.baseState && (e.baseState = !1, he = !0), e.memoizedState = n);
}
function Sd(e, t) {
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
function Va() {
  return ze().memoizedState;
}
function xd(e, t, n) {
  var r = pt(e);
  if (n = { lane: r, action: n, hasEagerState: !1, eagerState: null, next: null }, $a(e)) Ba(t, n);
  else if (n = xa(e, t, n, r), n !== null) {
    var l = fe();
    Fe(n, e, r, l), Ha(n, t, r);
  }
}
function Ed(e, t, n) {
  var r = pt(e), l = { lane: r, action: n, hasEagerState: !1, eagerState: null, next: null };
  if ($a(e)) Ba(t, l);
  else {
    var o = e.alternate;
    if (e.lanes === 0 && (o === null || o.lanes === 0) && (o = t.lastRenderedReducer, o !== null)) try {
      var i = t.lastRenderedState, u = o(i, n);
      if (l.hasEagerState = !0, l.eagerState = u, Ue(u, i)) {
        var s = t.interleaved;
        s === null ? (l.next = l, ki(t)) : (l.next = s.next, s.next = l), t.interleaved = l;
        return;
      }
    } catch {
    } finally {
    }
    n = xa(e, t, l, r), n !== null && (l = fe(), Fe(n, e, r, l), Ha(n, t, r));
  }
}
function $a(e) {
  var t = e.alternate;
  return e === W || t !== null && t === W;
}
function Ba(e, t) {
  Rn = el = !0;
  var n = e.pending;
  n === null ? t.next = t : (t.next = n.next, n.next = t), e.pending = t;
}
function Ha(e, t, n) {
  if (n & 4194240) {
    var r = t.lanes;
    r &= e.pendingLanes, n |= r, t.lanes = n, ii(e, n);
  }
}
var tl = { readContext: Te, useCallback: oe, useContext: oe, useEffect: oe, useImperativeHandle: oe, useInsertionEffect: oe, useLayoutEffect: oe, useMemo: oe, useReducer: oe, useRef: oe, useState: oe, useDebugValue: oe, useDeferredValue: oe, useTransition: oe, useMutableSource: oe, useSyncExternalStore: oe, useId: oe, unstable_isNewReconciler: !1 }, Cd = { readContext: Te, useCallback: function(e, t) {
  return Ve().memoizedState = [e, t === void 0 ? null : t], e;
}, useContext: Te, useEffect: zu, useImperativeHandle: function(e, t, n) {
  return n = n != null ? n.concat([e]) : null, Rr(
    4194308,
    4,
    Da.bind(null, t, e),
    n
  );
}, useLayoutEffect: function(e, t) {
  return Rr(4194308, 4, e, t);
}, useInsertionEffect: function(e, t) {
  return Rr(4, 2, e, t);
}, useMemo: function(e, t) {
  var n = Ve();
  return t = t === void 0 ? null : t, e = e(), n.memoizedState = [e, t], e;
}, useReducer: function(e, t, n) {
  var r = Ve();
  return t = n !== void 0 ? n(t) : t, r.memoizedState = r.baseState = t, e = { pending: null, interleaved: null, lanes: 0, dispatch: null, lastRenderedReducer: e, lastRenderedState: t }, r.queue = e, e = e.dispatch = xd.bind(null, W, e), [r.memoizedState, e];
}, useRef: function(e) {
  var t = Ve();
  return e = { current: e }, t.memoizedState = e;
}, useState: Tu, useDebugValue: Li, useDeferredValue: function(e) {
  return Ve().memoizedState = e;
}, useTransition: function() {
  var e = Tu(!1), t = e[0];
  return e = Sd.bind(null, e[1]), Ve().memoizedState = e, [t, e];
}, useMutableSource: function() {
}, useSyncExternalStore: function(e, t, n) {
  var r = W, l = Ve();
  if (B) {
    if (n === void 0) throw Error(S(407));
    n = n();
  } else {
    if (n = t(), te === null) throw Error(S(349));
    zt & 30 || Pa(r, t, n);
  }
  l.memoizedState = n;
  var o = { value: n, getSnapshot: t };
  return l.queue = o, zu(La.bind(
    null,
    r,
    o,
    e
  ), [e]), r.flags |= 2048, Jn(9, ja.bind(null, r, o, n, t), void 0, null), n;
}, useId: function() {
  var e = Ve(), t = te.identifierPrefix;
  if (B) {
    var n = Ye, r = Ke;
    n = (r & ~(1 << 32 - Oe(r) - 1)).toString(32) + n, t = ":" + t + "R" + n, n = Gn++, 0 < n && (t += "H" + n.toString(32)), t += ":";
  } else n = kd++, t = ":" + t + "r" + n.toString(32) + ":";
  return e.memoizedState = t;
}, unstable_isNewReconciler: !1 }, _d = {
  readContext: Te,
  useCallback: Fa,
  useContext: Te,
  useEffect: ji,
  useImperativeHandle: Oa,
  useInsertionEffect: Ma,
  useLayoutEffect: Ia,
  useMemo: Ua,
  useReducer: Wl,
  useRef: Ra,
  useState: function() {
    return Wl(Zn);
  },
  useDebugValue: Li,
  useDeferredValue: function(e) {
    var t = ze();
    return Aa(t, J.memoizedState, e);
  },
  useTransition: function() {
    var e = Wl(Zn)[0], t = ze().memoizedState;
    return [e, t];
  },
  useMutableSource: _a,
  useSyncExternalStore: Na,
  useId: Va,
  unstable_isNewReconciler: !1
}, Nd = { readContext: Te, useCallback: Fa, useContext: Te, useEffect: ji, useImperativeHandle: Oa, useInsertionEffect: Ma, useLayoutEffect: Ia, useMemo: Ua, useReducer: Kl, useRef: Ra, useState: function() {
  return Kl(Zn);
}, useDebugValue: Li, useDeferredValue: function(e) {
  var t = ze();
  return J === null ? t.memoizedState = e : Aa(t, J.memoizedState, e);
}, useTransition: function() {
  var e = Kl(Zn)[0], t = ze().memoizedState;
  return [e, t];
}, useMutableSource: _a, useSyncExternalStore: Na, useId: Va, unstable_isNewReconciler: !1 };
function Me(e, t) {
  if (e && e.defaultProps) {
    t = K({}, t), e = e.defaultProps;
    for (var n in e) t[n] === void 0 && (t[n] = e[n]);
    return t;
  }
  return t;
}
function Ro(e, t, n, r) {
  t = e.memoizedState, n = n(r, t), n = n == null ? t : K({}, t, n), e.memoizedState = n, e.lanes === 0 && (e.updateQueue.baseState = n);
}
var hl = { isMounted: function(e) {
  return (e = e._reactInternals) ? Dt(e) === e : !1;
}, enqueueSetState: function(e, t, n) {
  e = e._reactInternals;
  var r = fe(), l = pt(e), o = Xe(r, l);
  o.payload = t, n != null && (o.callback = n), t = ft(e, o, l), t !== null && (Fe(t, e, l, r), Tr(t, e, l));
}, enqueueReplaceState: function(e, t, n) {
  e = e._reactInternals;
  var r = fe(), l = pt(e), o = Xe(r, l);
  o.tag = 1, o.payload = t, n != null && (o.callback = n), t = ft(e, o, l), t !== null && (Fe(t, e, l, r), Tr(t, e, l));
}, enqueueForceUpdate: function(e, t) {
  e = e._reactInternals;
  var n = fe(), r = pt(e), l = Xe(n, r);
  l.tag = 2, t != null && (l.callback = t), t = ft(e, l, r), t !== null && (Fe(t, e, r, n), Tr(t, e, r));
} };
function Ru(e, t, n, r, l, o, i) {
  return e = e.stateNode, typeof e.shouldComponentUpdate == "function" ? e.shouldComponentUpdate(r, o, i) : t.prototype && t.prototype.isPureReactComponent ? !Hn(n, r) || !Hn(l, o) : !0;
}
function Qa(e, t, n) {
  var r = !1, l = vt, o = t.contextType;
  return typeof o == "object" && o !== null ? o = Te(o) : (l = ge(t) ? Lt : se.current, r = t.contextTypes, o = (r = r != null) ? nn(e, l) : vt), t = new t(n, o), e.memoizedState = t.state !== null && t.state !== void 0 ? t.state : null, t.updater = hl, e.stateNode = t, t._reactInternals = e, r && (e = e.stateNode, e.__reactInternalMemoizedUnmaskedChildContext = l, e.__reactInternalMemoizedMaskedChildContext = o), t;
}
function Mu(e, t, n, r) {
  e = t.state, typeof t.componentWillReceiveProps == "function" && t.componentWillReceiveProps(n, r), typeof t.UNSAFE_componentWillReceiveProps == "function" && t.UNSAFE_componentWillReceiveProps(n, r), t.state !== e && hl.enqueueReplaceState(t, t.state, null);
}
function Mo(e, t, n, r) {
  var l = e.stateNode;
  l.props = n, l.state = e.memoizedState, l.refs = {}, Si(e);
  var o = t.contextType;
  typeof o == "object" && o !== null ? l.context = Te(o) : (o = ge(t) ? Lt : se.current, l.context = nn(e, o)), l.state = e.memoizedState, o = t.getDerivedStateFromProps, typeof o == "function" && (Ro(e, t, o, n), l.state = e.memoizedState), typeof t.getDerivedStateFromProps == "function" || typeof l.getSnapshotBeforeUpdate == "function" || typeof l.UNSAFE_componentWillMount != "function" && typeof l.componentWillMount != "function" || (t = l.state, typeof l.componentWillMount == "function" && l.componentWillMount(), typeof l.UNSAFE_componentWillMount == "function" && l.UNSAFE_componentWillMount(), t !== l.state && hl.enqueueReplaceState(l, l.state, null), qr(e, n, l, r), l.state = e.memoizedState), typeof l.componentDidMount == "function" && (e.flags |= 4194308);
}
function un(e, t) {
  try {
    var n = "", r = t;
    do
      n += bc(r), r = r.return;
    while (r);
    var l = n;
  } catch (o) {
    l = `
Error generating stack: ` + o.message + `
` + o.stack;
  }
  return { value: e, source: t, stack: l, digest: null };
}
function Yl(e, t, n) {
  return { value: e, source: null, stack: n ?? null, digest: t ?? null };
}
function Io(e, t) {
  try {
    console.error(t.value);
  } catch (n) {
    setTimeout(function() {
      throw n;
    });
  }
}
var Pd = typeof WeakMap == "function" ? WeakMap : Map;
function Wa(e, t, n) {
  n = Xe(-1, n), n.tag = 3, n.payload = { element: null };
  var r = t.value;
  return n.callback = function() {
    rl || (rl = !0, Qo = r), Io(e, t);
  }, n;
}
function Ka(e, t, n) {
  n = Xe(-1, n), n.tag = 3;
  var r = e.type.getDerivedStateFromError;
  if (typeof r == "function") {
    var l = t.value;
    n.payload = function() {
      return r(l);
    }, n.callback = function() {
      Io(e, t);
    };
  }
  var o = e.stateNode;
  return o !== null && typeof o.componentDidCatch == "function" && (n.callback = function() {
    Io(e, t), typeof r != "function" && (dt === null ? dt = /* @__PURE__ */ new Set([this]) : dt.add(this));
    var i = t.stack;
    this.componentDidCatch(t.value, { componentStack: i !== null ? i : "" });
  }), n;
}
function Iu(e, t, n) {
  var r = e.pingCache;
  if (r === null) {
    r = e.pingCache = new Pd();
    var l = /* @__PURE__ */ new Set();
    r.set(t, l);
  } else l = r.get(t), l === void 0 && (l = /* @__PURE__ */ new Set(), r.set(t, l));
  l.has(n) || (l.add(n), e = $d.bind(null, e, t, n), t.then(e, e));
}
function Du(e) {
  do {
    var t;
    if ((t = e.tag === 13) && (t = e.memoizedState, t = t !== null ? t.dehydrated !== null : !0), t) return e;
    e = e.return;
  } while (e !== null);
  return null;
}
function Ou(e, t, n, r, l) {
  return e.mode & 1 ? (e.flags |= 65536, e.lanes = l, e) : (e === t ? e.flags |= 65536 : (e.flags |= 128, n.flags |= 131072, n.flags &= -52805, n.tag === 1 && (n.alternate === null ? n.tag = 17 : (t = Xe(-1, 1), t.tag = 2, ft(n, t, 1))), n.lanes |= 1), e);
}
var jd = be.ReactCurrentOwner, he = !1;
function ce(e, t, n, r) {
  t.child = e === null ? Sa(t, null, n, r) : ln(t, e.child, n, r);
}
function Fu(e, t, n, r, l) {
  n = n.render;
  var o = t.ref;
  return bt(t, l), r = Ni(e, t, n, r, o, l), n = Pi(), e !== null && !he ? (t.updateQueue = e.updateQueue, t.flags &= -2053, e.lanes &= ~l, qe(e, t, l)) : (B && n && mi(t), t.flags |= 1, ce(e, t, r, l), t.child);
}
function Uu(e, t, n, r, l) {
  if (e === null) {
    var o = n.type;
    return typeof o == "function" && !Fi(o) && o.defaultProps === void 0 && n.compare === null && n.defaultProps === void 0 ? (t.tag = 15, t.type = o, Ya(e, t, o, r, l)) : (e = Or(n.type, null, r, t, t.mode, l), e.ref = t.ref, e.return = t, t.child = e);
  }
  if (o = e.child, !(e.lanes & l)) {
    var i = o.memoizedProps;
    if (n = n.compare, n = n !== null ? n : Hn, n(i, r) && e.ref === t.ref) return qe(e, t, l);
  }
  return t.flags |= 1, e = mt(o, r), e.ref = t.ref, e.return = t, t.child = e;
}
function Ya(e, t, n, r, l) {
  if (e !== null) {
    var o = e.memoizedProps;
    if (Hn(o, r) && e.ref === t.ref) if (he = !1, t.pendingProps = r = o, (e.lanes & l) !== 0) e.flags & 131072 && (he = !0);
    else return t.lanes = e.lanes, qe(e, t, l);
  }
  return Do(e, t, n, r, l);
}
function Xa(e, t, n) {
  var r = t.pendingProps, l = r.children, o = e !== null ? e.memoizedState : null;
  if (r.mode === "hidden") if (!(t.mode & 1)) t.memoizedState = { baseLanes: 0, cachePool: null, transitions: null }, U(Xt, we), we |= n;
  else {
    if (!(n & 1073741824)) return e = o !== null ? o.baseLanes | n : n, t.lanes = t.childLanes = 1073741824, t.memoizedState = { baseLanes: e, cachePool: null, transitions: null }, t.updateQueue = null, U(Xt, we), we |= e, null;
    t.memoizedState = { baseLanes: 0, cachePool: null, transitions: null }, r = o !== null ? o.baseLanes : n, U(Xt, we), we |= r;
  }
  else o !== null ? (r = o.baseLanes | n, t.memoizedState = null) : r = n, U(Xt, we), we |= r;
  return ce(e, t, l, n), t.child;
}
function Ga(e, t) {
  var n = t.ref;
  (e === null && n !== null || e !== null && e.ref !== n) && (t.flags |= 512, t.flags |= 2097152);
}
function Do(e, t, n, r, l) {
  var o = ge(n) ? Lt : se.current;
  return o = nn(t, o), bt(t, l), n = Ni(e, t, n, r, o, l), r = Pi(), e !== null && !he ? (t.updateQueue = e.updateQueue, t.flags &= -2053, e.lanes &= ~l, qe(e, t, l)) : (B && r && mi(t), t.flags |= 1, ce(e, t, n, l), t.child);
}
function Au(e, t, n, r, l) {
  if (ge(n)) {
    var o = !0;
    Yr(t);
  } else o = !1;
  if (bt(t, l), t.stateNode === null) Mr(e, t), Qa(t, n, r), Mo(t, n, r, l), r = !0;
  else if (e === null) {
    var i = t.stateNode, u = t.memoizedProps;
    i.props = u;
    var s = i.context, c = n.contextType;
    typeof c == "object" && c !== null ? c = Te(c) : (c = ge(n) ? Lt : se.current, c = nn(t, c));
    var g = n.getDerivedStateFromProps, h = typeof g == "function" || typeof i.getSnapshotBeforeUpdate == "function";
    h || typeof i.UNSAFE_componentWillReceiveProps != "function" && typeof i.componentWillReceiveProps != "function" || (u !== r || s !== c) && Mu(t, i, r, c), nt = !1;
    var f = t.memoizedState;
    i.state = f, qr(t, r, i, l), s = t.memoizedState, u !== r || f !== s || ve.current || nt ? (typeof g == "function" && (Ro(t, n, g, r), s = t.memoizedState), (u = nt || Ru(t, n, u, r, f, s, c)) ? (h || typeof i.UNSAFE_componentWillMount != "function" && typeof i.componentWillMount != "function" || (typeof i.componentWillMount == "function" && i.componentWillMount(), typeof i.UNSAFE_componentWillMount == "function" && i.UNSAFE_componentWillMount()), typeof i.componentDidMount == "function" && (t.flags |= 4194308)) : (typeof i.componentDidMount == "function" && (t.flags |= 4194308), t.memoizedProps = r, t.memoizedState = s), i.props = r, i.state = s, i.context = c, r = u) : (typeof i.componentDidMount == "function" && (t.flags |= 4194308), r = !1);
  } else {
    i = t.stateNode, Ea(e, t), u = t.memoizedProps, c = t.type === t.elementType ? u : Me(t.type, u), i.props = c, h = t.pendingProps, f = i.context, s = n.contextType, typeof s == "object" && s !== null ? s = Te(s) : (s = ge(n) ? Lt : se.current, s = nn(t, s));
    var w = n.getDerivedStateFromProps;
    (g = typeof w == "function" || typeof i.getSnapshotBeforeUpdate == "function") || typeof i.UNSAFE_componentWillReceiveProps != "function" && typeof i.componentWillReceiveProps != "function" || (u !== h || f !== s) && Mu(t, i, r, s), nt = !1, f = t.memoizedState, i.state = f, qr(t, r, i, l);
    var k = t.memoizedState;
    u !== h || f !== k || ve.current || nt ? (typeof w == "function" && (Ro(t, n, w, r), k = t.memoizedState), (c = nt || Ru(t, n, c, r, f, k, s) || !1) ? (g || typeof i.UNSAFE_componentWillUpdate != "function" && typeof i.componentWillUpdate != "function" || (typeof i.componentWillUpdate == "function" && i.componentWillUpdate(r, k, s), typeof i.UNSAFE_componentWillUpdate == "function" && i.UNSAFE_componentWillUpdate(r, k, s)), typeof i.componentDidUpdate == "function" && (t.flags |= 4), typeof i.getSnapshotBeforeUpdate == "function" && (t.flags |= 1024)) : (typeof i.componentDidUpdate != "function" || u === e.memoizedProps && f === e.memoizedState || (t.flags |= 4), typeof i.getSnapshotBeforeUpdate != "function" || u === e.memoizedProps && f === e.memoizedState || (t.flags |= 1024), t.memoizedProps = r, t.memoizedState = k), i.props = r, i.state = k, i.context = s, r = c) : (typeof i.componentDidUpdate != "function" || u === e.memoizedProps && f === e.memoizedState || (t.flags |= 4), typeof i.getSnapshotBeforeUpdate != "function" || u === e.memoizedProps && f === e.memoizedState || (t.flags |= 1024), r = !1);
  }
  return Oo(e, t, n, r, o, l);
}
function Oo(e, t, n, r, l, o) {
  Ga(e, t);
  var i = (t.flags & 128) !== 0;
  if (!r && !i) return l && Cu(t, n, !1), qe(e, t, o);
  r = t.stateNode, jd.current = t;
  var u = i && typeof n.getDerivedStateFromError != "function" ? null : r.render();
  return t.flags |= 1, e !== null && i ? (t.child = ln(t, e.child, null, o), t.child = ln(t, null, u, o)) : ce(e, t, u, o), t.memoizedState = r.state, l && Cu(t, n, !0), t.child;
}
function Za(e) {
  var t = e.stateNode;
  t.pendingContext ? Eu(e, t.pendingContext, t.pendingContext !== t.context) : t.context && Eu(e, t.context, !1), xi(e, t.containerInfo);
}
function Vu(e, t, n, r, l) {
  return rn(), vi(l), t.flags |= 256, ce(e, t, n, r), t.child;
}
var Fo = { dehydrated: null, treeContext: null, retryLane: 0 };
function Uo(e) {
  return { baseLanes: e, cachePool: null, transitions: null };
}
function Ja(e, t, n) {
  var r = t.pendingProps, l = Q.current, o = !1, i = (t.flags & 128) !== 0, u;
  if ((u = i) || (u = e !== null && e.memoizedState === null ? !1 : (l & 2) !== 0), u ? (o = !0, t.flags &= -129) : (e === null || e.memoizedState !== null) && (l |= 1), U(Q, l & 1), e === null)
    return To(t), e = t.memoizedState, e !== null && (e = e.dehydrated, e !== null) ? (t.mode & 1 ? e.data === "$!" ? t.lanes = 8 : t.lanes = 1073741824 : t.lanes = 1, null) : (i = r.children, e = r.fallback, o ? (r = t.mode, o = t.child, i = { mode: "hidden", children: i }, !(r & 1) && o !== null ? (o.childLanes = 0, o.pendingProps = i) : o = yl(i, r, 0, null), e = jt(e, r, n, null), o.return = t, e.return = t, o.sibling = e, t.child = o, t.child.memoizedState = Uo(n), t.memoizedState = Fo, e) : Ti(t, i));
  if (l = e.memoizedState, l !== null && (u = l.dehydrated, u !== null)) return Ld(e, t, i, r, u, l, n);
  if (o) {
    o = r.fallback, i = t.mode, l = e.child, u = l.sibling;
    var s = { mode: "hidden", children: r.children };
    return !(i & 1) && t.child !== l ? (r = t.child, r.childLanes = 0, r.pendingProps = s, t.deletions = null) : (r = mt(l, s), r.subtreeFlags = l.subtreeFlags & 14680064), u !== null ? o = mt(u, o) : (o = jt(o, i, n, null), o.flags |= 2), o.return = t, r.return = t, r.sibling = o, t.child = r, r = o, o = t.child, i = e.child.memoizedState, i = i === null ? Uo(n) : { baseLanes: i.baseLanes | n, cachePool: null, transitions: i.transitions }, o.memoizedState = i, o.childLanes = e.childLanes & ~n, t.memoizedState = Fo, r;
  }
  return o = e.child, e = o.sibling, r = mt(o, { mode: "visible", children: r.children }), !(t.mode & 1) && (r.lanes = n), r.return = t, r.sibling = null, e !== null && (n = t.deletions, n === null ? (t.deletions = [e], t.flags |= 16) : n.push(e)), t.child = r, t.memoizedState = null, r;
}
function Ti(e, t) {
  return t = yl({ mode: "visible", children: t }, e.mode, 0, null), t.return = e, e.child = t;
}
function Sr(e, t, n, r) {
  return r !== null && vi(r), ln(t, e.child, null, n), e = Ti(t, t.pendingProps.children), e.flags |= 2, t.memoizedState = null, e;
}
function Ld(e, t, n, r, l, o, i) {
  if (n)
    return t.flags & 256 ? (t.flags &= -257, r = Yl(Error(S(422))), Sr(e, t, i, r)) : t.memoizedState !== null ? (t.child = e.child, t.flags |= 128, null) : (o = r.fallback, l = t.mode, r = yl({ mode: "visible", children: r.children }, l, 0, null), o = jt(o, l, i, null), o.flags |= 2, r.return = t, o.return = t, r.sibling = o, t.child = r, t.mode & 1 && ln(t, e.child, null, i), t.child.memoizedState = Uo(i), t.memoizedState = Fo, o);
  if (!(t.mode & 1)) return Sr(e, t, i, null);
  if (l.data === "$!") {
    if (r = l.nextSibling && l.nextSibling.dataset, r) var u = r.dgst;
    return r = u, o = Error(S(419)), r = Yl(o, r, void 0), Sr(e, t, i, r);
  }
  if (u = (i & e.childLanes) !== 0, he || u) {
    if (r = te, r !== null) {
      switch (i & -i) {
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
      l = l & (r.suspendedLanes | i) ? 0 : l, l !== 0 && l !== o.retryLane && (o.retryLane = l, Je(e, l), Fe(r, e, l, -1));
    }
    return Oi(), r = Yl(Error(S(421))), Sr(e, t, i, r);
  }
  return l.data === "$?" ? (t.flags |= 128, t.child = e.child, t = Bd.bind(null, e), l._reactRetry = t, null) : (e = o.treeContext, ke = ct(l.nextSibling), Se = t, B = !0, De = null, e !== null && (Ne[Pe++] = Ke, Ne[Pe++] = Ye, Ne[Pe++] = Tt, Ke = e.id, Ye = e.overflow, Tt = t), t = Ti(t, r.children), t.flags |= 4096, t);
}
function $u(e, t, n) {
  e.lanes |= t;
  var r = e.alternate;
  r !== null && (r.lanes |= t), zo(e.return, t, n);
}
function Xl(e, t, n, r, l) {
  var o = e.memoizedState;
  o === null ? e.memoizedState = { isBackwards: t, rendering: null, renderingStartTime: 0, last: r, tail: n, tailMode: l } : (o.isBackwards = t, o.rendering = null, o.renderingStartTime = 0, o.last = r, o.tail = n, o.tailMode = l);
}
function qa(e, t, n) {
  var r = t.pendingProps, l = r.revealOrder, o = r.tail;
  if (ce(e, t, r.children, n), r = Q.current, r & 2) r = r & 1 | 2, t.flags |= 128;
  else {
    if (e !== null && e.flags & 128) e: for (e = t.child; e !== null; ) {
      if (e.tag === 13) e.memoizedState !== null && $u(e, n, t);
      else if (e.tag === 19) $u(e, n, t);
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
      n = l, n === null ? (l = t.child, t.child = null) : (l = n.sibling, n.sibling = null), Xl(t, !1, l, n, o);
      break;
    case "backwards":
      for (n = null, l = t.child, t.child = null; l !== null; ) {
        if (e = l.alternate, e !== null && br(e) === null) {
          t.child = l;
          break;
        }
        e = l.sibling, l.sibling = n, n = l, l = e;
      }
      Xl(t, !0, n, null, o);
      break;
    case "together":
      Xl(t, !1, null, null, void 0);
      break;
    default:
      t.memoizedState = null;
  }
  return t.child;
}
function Mr(e, t) {
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
function Td(e, t, n) {
  switch (t.tag) {
    case 3:
      Za(t), rn();
      break;
    case 5:
      Ca(t);
      break;
    case 1:
      ge(t.type) && Yr(t);
      break;
    case 4:
      xi(t, t.stateNode.containerInfo);
      break;
    case 10:
      var r = t.type._context, l = t.memoizedProps.value;
      U(Zr, r._currentValue), r._currentValue = l;
      break;
    case 13:
      if (r = t.memoizedState, r !== null)
        return r.dehydrated !== null ? (U(Q, Q.current & 1), t.flags |= 128, null) : n & t.child.childLanes ? Ja(e, t, n) : (U(Q, Q.current & 1), e = qe(e, t, n), e !== null ? e.sibling : null);
      U(Q, Q.current & 1);
      break;
    case 19:
      if (r = (n & t.childLanes) !== 0, e.flags & 128) {
        if (r) return qa(e, t, n);
        t.flags |= 128;
      }
      if (l = t.memoizedState, l !== null && (l.rendering = null, l.tail = null, l.lastEffect = null), U(Q, Q.current), r) break;
      return null;
    case 22:
    case 23:
      return t.lanes = 0, Xa(e, t, n);
  }
  return qe(e, t, n);
}
var ba, Ao, ec, tc;
ba = function(e, t) {
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
Ao = function() {
};
ec = function(e, t, n, r) {
  var l = e.memoizedProps;
  if (l !== r) {
    e = t.stateNode, Nt(He.current);
    var o = null;
    switch (n) {
      case "input":
        l = io(e, l), r = io(e, r), o = [];
        break;
      case "select":
        l = K({}, l, { value: void 0 }), r = K({}, r, { value: void 0 }), o = [];
        break;
      case "textarea":
        l = ao(e, l), r = ao(e, r), o = [];
        break;
      default:
        typeof l.onClick != "function" && typeof r.onClick == "function" && (e.onclick = Wr);
    }
    fo(n, r);
    var i;
    n = null;
    for (c in l) if (!r.hasOwnProperty(c) && l.hasOwnProperty(c) && l[c] != null) if (c === "style") {
      var u = l[c];
      for (i in u) u.hasOwnProperty(i) && (n || (n = {}), n[i] = "");
    } else c !== "dangerouslySetInnerHTML" && c !== "children" && c !== "suppressContentEditableWarning" && c !== "suppressHydrationWarning" && c !== "autoFocus" && (On.hasOwnProperty(c) ? o || (o = []) : (o = o || []).push(c, null));
    for (c in r) {
      var s = r[c];
      if (u = l != null ? l[c] : void 0, r.hasOwnProperty(c) && s !== u && (s != null || u != null)) if (c === "style") if (u) {
        for (i in u) !u.hasOwnProperty(i) || s && s.hasOwnProperty(i) || (n || (n = {}), n[i] = "");
        for (i in s) s.hasOwnProperty(i) && u[i] !== s[i] && (n || (n = {}), n[i] = s[i]);
      } else n || (o || (o = []), o.push(
        c,
        n
      )), n = s;
      else c === "dangerouslySetInnerHTML" ? (s = s ? s.__html : void 0, u = u ? u.__html : void 0, s != null && u !== s && (o = o || []).push(c, s)) : c === "children" ? typeof s != "string" && typeof s != "number" || (o = o || []).push(c, "" + s) : c !== "suppressContentEditableWarning" && c !== "suppressHydrationWarning" && (On.hasOwnProperty(c) ? (s != null && c === "onScroll" && V("scroll", e), o || u === s || (o = [])) : (o = o || []).push(c, s));
    }
    n && (o = o || []).push("style", n);
    var c = o;
    (t.updateQueue = c) && (t.flags |= 4);
  }
};
tc = function(e, t, n, r) {
  n !== r && (t.flags |= 4);
};
function Sn(e, t) {
  if (!B) switch (e.tailMode) {
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
function ie(e) {
  var t = e.alternate !== null && e.alternate.child === e.child, n = 0, r = 0;
  if (t) for (var l = e.child; l !== null; ) n |= l.lanes | l.childLanes, r |= l.subtreeFlags & 14680064, r |= l.flags & 14680064, l.return = e, l = l.sibling;
  else for (l = e.child; l !== null; ) n |= l.lanes | l.childLanes, r |= l.subtreeFlags, r |= l.flags, l.return = e, l = l.sibling;
  return e.subtreeFlags |= r, e.childLanes = n, t;
}
function zd(e, t, n) {
  var r = t.pendingProps;
  switch (hi(t), t.tag) {
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
      return ie(t), null;
    case 1:
      return ge(t.type) && Kr(), ie(t), null;
    case 3:
      return r = t.stateNode, on(), $(ve), $(se), Ci(), r.pendingContext && (r.context = r.pendingContext, r.pendingContext = null), (e === null || e.child === null) && (wr(t) ? t.flags |= 4 : e === null || e.memoizedState.isDehydrated && !(t.flags & 256) || (t.flags |= 1024, De !== null && (Yo(De), De = null))), Ao(e, t), ie(t), null;
    case 5:
      Ei(t);
      var l = Nt(Xn.current);
      if (n = t.type, e !== null && t.stateNode != null) ec(e, t, n, r, l), e.ref !== t.ref && (t.flags |= 512, t.flags |= 2097152);
      else {
        if (!r) {
          if (t.stateNode === null) throw Error(S(166));
          return ie(t), null;
        }
        if (e = Nt(He.current), wr(t)) {
          r = t.stateNode, n = t.type;
          var o = t.memoizedProps;
          switch (r[$e] = t, r[Kn] = o, e = (t.mode & 1) !== 0, n) {
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
              Zi(r, o), V("invalid", r);
              break;
            case "select":
              r._wrapperState = { wasMultiple: !!o.multiple }, V("invalid", r);
              break;
            case "textarea":
              qi(r, o), V("invalid", r);
          }
          fo(n, o), l = null;
          for (var i in o) if (o.hasOwnProperty(i)) {
            var u = o[i];
            i === "children" ? typeof u == "string" ? r.textContent !== u && (o.suppressHydrationWarning !== !0 && yr(r.textContent, u, e), l = ["children", u]) : typeof u == "number" && r.textContent !== "" + u && (o.suppressHydrationWarning !== !0 && yr(
              r.textContent,
              u,
              e
            ), l = ["children", "" + u]) : On.hasOwnProperty(i) && u != null && i === "onScroll" && V("scroll", r);
          }
          switch (n) {
            case "input":
              cr(r), Ji(r, o, !0);
              break;
            case "textarea":
              cr(r), bi(r);
              break;
            case "select":
            case "option":
              break;
            default:
              typeof o.onClick == "function" && (r.onclick = Wr);
          }
          r = l, t.updateQueue = r, r !== null && (t.flags |= 4);
        } else {
          i = l.nodeType === 9 ? l : l.ownerDocument, e === "http://www.w3.org/1999/xhtml" && (e = Ls(n)), e === "http://www.w3.org/1999/xhtml" ? n === "script" ? (e = i.createElement("div"), e.innerHTML = "<script><\/script>", e = e.removeChild(e.firstChild)) : typeof r.is == "string" ? e = i.createElement(n, { is: r.is }) : (e = i.createElement(n), n === "select" && (i = e, r.multiple ? i.multiple = !0 : r.size && (i.size = r.size))) : e = i.createElementNS(e, n), e[$e] = t, e[Kn] = r, ba(e, t, !1, !1), t.stateNode = e;
          e: {
            switch (i = po(n, r), n) {
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
                Zi(e, r), l = io(e, r), V("invalid", e);
                break;
              case "option":
                l = r;
                break;
              case "select":
                e._wrapperState = { wasMultiple: !!r.multiple }, l = K({}, r, { value: void 0 }), V("invalid", e);
                break;
              case "textarea":
                qi(e, r), l = ao(e, r), V("invalid", e);
                break;
              default:
                l = r;
            }
            fo(n, l), u = l;
            for (o in u) if (u.hasOwnProperty(o)) {
              var s = u[o];
              o === "style" ? Rs(e, s) : o === "dangerouslySetInnerHTML" ? (s = s ? s.__html : void 0, s != null && Ts(e, s)) : o === "children" ? typeof s == "string" ? (n !== "textarea" || s !== "") && Fn(e, s) : typeof s == "number" && Fn(e, "" + s) : o !== "suppressContentEditableWarning" && o !== "suppressHydrationWarning" && o !== "autoFocus" && (On.hasOwnProperty(o) ? s != null && o === "onScroll" && V("scroll", e) : s != null && ei(e, o, s, i));
            }
            switch (n) {
              case "input":
                cr(e), Ji(e, r, !1);
                break;
              case "textarea":
                cr(e), bi(e);
                break;
              case "option":
                r.value != null && e.setAttribute("value", "" + ht(r.value));
                break;
              case "select":
                e.multiple = !!r.multiple, o = r.value, o != null ? Gt(e, !!r.multiple, o, !1) : r.defaultValue != null && Gt(
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
      return ie(t), null;
    case 6:
      if (e && t.stateNode != null) tc(e, t, e.memoizedProps, r);
      else {
        if (typeof r != "string" && t.stateNode === null) throw Error(S(166));
        if (n = Nt(Xn.current), Nt(He.current), wr(t)) {
          if (r = t.stateNode, n = t.memoizedProps, r[$e] = t, (o = r.nodeValue !== n) && (e = Se, e !== null)) switch (e.tag) {
            case 3:
              yr(r.nodeValue, n, (e.mode & 1) !== 0);
              break;
            case 5:
              e.memoizedProps.suppressHydrationWarning !== !0 && yr(r.nodeValue, n, (e.mode & 1) !== 0);
          }
          o && (t.flags |= 4);
        } else r = (n.nodeType === 9 ? n : n.ownerDocument).createTextNode(r), r[$e] = t, t.stateNode = r;
      }
      return ie(t), null;
    case 13:
      if ($(Q), r = t.memoizedState, e === null || e.memoizedState !== null && e.memoizedState.dehydrated !== null) {
        if (B && ke !== null && t.mode & 1 && !(t.flags & 128)) wa(), rn(), t.flags |= 98560, o = !1;
        else if (o = wr(t), r !== null && r.dehydrated !== null) {
          if (e === null) {
            if (!o) throw Error(S(318));
            if (o = t.memoizedState, o = o !== null ? o.dehydrated : null, !o) throw Error(S(317));
            o[$e] = t;
          } else rn(), !(t.flags & 128) && (t.memoizedState = null), t.flags |= 4;
          ie(t), o = !1;
        } else De !== null && (Yo(De), De = null), o = !0;
        if (!o) return t.flags & 65536 ? t : null;
      }
      return t.flags & 128 ? (t.lanes = n, t) : (r = r !== null, r !== (e !== null && e.memoizedState !== null) && r && (t.child.flags |= 8192, t.mode & 1 && (e === null || Q.current & 1 ? q === 0 && (q = 3) : Oi())), t.updateQueue !== null && (t.flags |= 4), ie(t), null);
    case 4:
      return on(), Ao(e, t), e === null && Qn(t.stateNode.containerInfo), ie(t), null;
    case 10:
      return wi(t.type._context), ie(t), null;
    case 17:
      return ge(t.type) && Kr(), ie(t), null;
    case 19:
      if ($(Q), o = t.memoizedState, o === null) return ie(t), null;
      if (r = (t.flags & 128) !== 0, i = o.rendering, i === null) if (r) Sn(o, !1);
      else {
        if (q !== 0 || e !== null && e.flags & 128) for (e = t.child; e !== null; ) {
          if (i = br(e), i !== null) {
            for (t.flags |= 128, Sn(o, !1), r = i.updateQueue, r !== null && (t.updateQueue = r, t.flags |= 4), t.subtreeFlags = 0, r = n, n = t.child; n !== null; ) o = n, e = r, o.flags &= 14680066, i = o.alternate, i === null ? (o.childLanes = 0, o.lanes = e, o.child = null, o.subtreeFlags = 0, o.memoizedProps = null, o.memoizedState = null, o.updateQueue = null, o.dependencies = null, o.stateNode = null) : (o.childLanes = i.childLanes, o.lanes = i.lanes, o.child = i.child, o.subtreeFlags = 0, o.deletions = null, o.memoizedProps = i.memoizedProps, o.memoizedState = i.memoizedState, o.updateQueue = i.updateQueue, o.type = i.type, e = i.dependencies, o.dependencies = e === null ? null : { lanes: e.lanes, firstContext: e.firstContext }), n = n.sibling;
            return U(Q, Q.current & 1 | 2), t.child;
          }
          e = e.sibling;
        }
        o.tail !== null && G() > sn && (t.flags |= 128, r = !0, Sn(o, !1), t.lanes = 4194304);
      }
      else {
        if (!r) if (e = br(i), e !== null) {
          if (t.flags |= 128, r = !0, n = e.updateQueue, n !== null && (t.updateQueue = n, t.flags |= 4), Sn(o, !0), o.tail === null && o.tailMode === "hidden" && !i.alternate && !B) return ie(t), null;
        } else 2 * G() - o.renderingStartTime > sn && n !== 1073741824 && (t.flags |= 128, r = !0, Sn(o, !1), t.lanes = 4194304);
        o.isBackwards ? (i.sibling = t.child, t.child = i) : (n = o.last, n !== null ? n.sibling = i : t.child = i, o.last = i);
      }
      return o.tail !== null ? (t = o.tail, o.rendering = t, o.tail = t.sibling, o.renderingStartTime = G(), t.sibling = null, n = Q.current, U(Q, r ? n & 1 | 2 : n & 1), t) : (ie(t), null);
    case 22:
    case 23:
      return Di(), r = t.memoizedState !== null, e !== null && e.memoizedState !== null !== r && (t.flags |= 8192), r && t.mode & 1 ? we & 1073741824 && (ie(t), t.subtreeFlags & 6 && (t.flags |= 8192)) : ie(t), null;
    case 24:
      return null;
    case 25:
      return null;
  }
  throw Error(S(156, t.tag));
}
function Rd(e, t) {
  switch (hi(t), t.tag) {
    case 1:
      return ge(t.type) && Kr(), e = t.flags, e & 65536 ? (t.flags = e & -65537 | 128, t) : null;
    case 3:
      return on(), $(ve), $(se), Ci(), e = t.flags, e & 65536 && !(e & 128) ? (t.flags = e & -65537 | 128, t) : null;
    case 5:
      return Ei(t), null;
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
      return wi(t.type._context), null;
    case 22:
    case 23:
      return Di(), null;
    case 24:
      return null;
    default:
      return null;
  }
}
var xr = !1, ue = !1, Md = typeof WeakSet == "function" ? WeakSet : Set, L = null;
function Yt(e, t) {
  var n = e.ref;
  if (n !== null) if (typeof n == "function") try {
    n(null);
  } catch (r) {
    Y(e, t, r);
  }
  else n.current = null;
}
function Vo(e, t, n) {
  try {
    n();
  } catch (r) {
    Y(e, t, r);
  }
}
var Bu = !1;
function Id(e, t) {
  if (Eo = Br, e = ia(), pi(e)) {
    if ("selectionStart" in e) var n = { start: e.selectionStart, end: e.selectionEnd };
    else e: {
      n = (n = e.ownerDocument) && n.defaultView || window;
      var r = n.getSelection && n.getSelection();
      if (r && r.rangeCount !== 0) {
        n = r.anchorNode;
        var l = r.anchorOffset, o = r.focusNode;
        r = r.focusOffset;
        try {
          n.nodeType, o.nodeType;
        } catch {
          n = null;
          break e;
        }
        var i = 0, u = -1, s = -1, c = 0, g = 0, h = e, f = null;
        t: for (; ; ) {
          for (var w; h !== n || l !== 0 && h.nodeType !== 3 || (u = i + l), h !== o || r !== 0 && h.nodeType !== 3 || (s = i + r), h.nodeType === 3 && (i += h.nodeValue.length), (w = h.firstChild) !== null; )
            f = h, h = w;
          for (; ; ) {
            if (h === e) break t;
            if (f === n && ++c === l && (u = i), f === o && ++g === r && (s = i), (w = h.nextSibling) !== null) break;
            h = f, f = h.parentNode;
          }
          h = w;
        }
        n = u === -1 || s === -1 ? null : { start: u, end: s };
      } else n = null;
    }
    n = n || { start: 0, end: 0 };
  } else n = null;
  for (Co = { focusedElem: e, selectionRange: n }, Br = !1, L = t; L !== null; ) if (t = L, e = t.child, (t.subtreeFlags & 1028) !== 0 && e !== null) e.return = t, L = e;
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
            var E = k.memoizedProps, z = k.memoizedState, d = t.stateNode, a = d.getSnapshotBeforeUpdate(t.elementType === t.type ? E : Me(t.type, E), z);
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
    } catch (v) {
      Y(t, t.return, v);
    }
    if (e = t.sibling, e !== null) {
      e.return = t.return, L = e;
      break;
    }
    L = t.return;
  }
  return k = Bu, Bu = !1, k;
}
function Mn(e, t, n) {
  var r = t.updateQueue;
  if (r = r !== null ? r.lastEffect : null, r !== null) {
    var l = r = r.next;
    do {
      if ((l.tag & e) === e) {
        var o = l.destroy;
        l.destroy = void 0, o !== void 0 && Vo(t, n, o);
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
function $o(e) {
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
function nc(e) {
  var t = e.alternate;
  t !== null && (e.alternate = null, nc(t)), e.child = null, e.deletions = null, e.sibling = null, e.tag === 5 && (t = e.stateNode, t !== null && (delete t[$e], delete t[Kn], delete t[Po], delete t[vd], delete t[gd])), e.stateNode = null, e.return = null, e.dependencies = null, e.memoizedProps = null, e.memoizedState = null, e.pendingProps = null, e.stateNode = null, e.updateQueue = null;
}
function rc(e) {
  return e.tag === 5 || e.tag === 3 || e.tag === 4;
}
function Hu(e) {
  e: for (; ; ) {
    for (; e.sibling === null; ) {
      if (e.return === null || rc(e.return)) return null;
      e = e.return;
    }
    for (e.sibling.return = e.return, e = e.sibling; e.tag !== 5 && e.tag !== 6 && e.tag !== 18; ) {
      if (e.flags & 2 || e.child === null || e.tag === 4) continue e;
      e.child.return = e, e = e.child;
    }
    if (!(e.flags & 2)) return e.stateNode;
  }
}
function Bo(e, t, n) {
  var r = e.tag;
  if (r === 5 || r === 6) e = e.stateNode, t ? n.nodeType === 8 ? n.parentNode.insertBefore(e, t) : n.insertBefore(e, t) : (n.nodeType === 8 ? (t = n.parentNode, t.insertBefore(e, n)) : (t = n, t.appendChild(e)), n = n._reactRootContainer, n != null || t.onclick !== null || (t.onclick = Wr));
  else if (r !== 4 && (e = e.child, e !== null)) for (Bo(e, t, n), e = e.sibling; e !== null; ) Bo(e, t, n), e = e.sibling;
}
function Ho(e, t, n) {
  var r = e.tag;
  if (r === 5 || r === 6) e = e.stateNode, t ? n.insertBefore(e, t) : n.appendChild(e);
  else if (r !== 4 && (e = e.child, e !== null)) for (Ho(e, t, n), e = e.sibling; e !== null; ) Ho(e, t, n), e = e.sibling;
}
var ne = null, Ie = !1;
function et(e, t, n) {
  for (n = n.child; n !== null; ) lc(e, t, n), n = n.sibling;
}
function lc(e, t, n) {
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
          var o = l, i = o.destroy;
          o = o.tag, i !== void 0 && (o & 2 || o & 4) && Vo(n, t, i), l = l.next;
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
function Qu(e) {
  var t = e.updateQueue;
  if (t !== null) {
    e.updateQueue = null;
    var n = e.stateNode;
    n === null && (n = e.stateNode = new Md()), t.forEach(function(r) {
      var l = Hd.bind(null, e, r);
      n.has(r) || (n.add(r), r.then(l, l));
    });
  }
}
function Re(e, t) {
  var n = t.deletions;
  if (n !== null) for (var r = 0; r < n.length; r++) {
    var l = n[r];
    try {
      var o = e, i = t, u = i;
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
      lc(o, i, l), ne = null, Ie = !1;
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
        var o = e.memoizedProps, i = n !== null ? n.memoizedProps : o, u = e.type, s = e.updateQueue;
        if (e.updateQueue = null, s !== null) try {
          u === "input" && o.type === "radio" && o.name != null && Ps(l, o), po(u, i);
          var c = po(u, o);
          for (i = 0; i < s.length; i += 2) {
            var g = s[i], h = s[i + 1];
            g === "style" ? Rs(l, h) : g === "dangerouslySetInnerHTML" ? Ts(l, h) : g === "children" ? Fn(l, h) : ei(l, g, h, c);
          }
          switch (u) {
            case "input":
              uo(l, o);
              break;
            case "textarea":
              js(l, o);
              break;
            case "select":
              var f = l._wrapperState.wasMultiple;
              l._wrapperState.wasMultiple = !!o.multiple;
              var w = o.value;
              w != null ? Gt(l, !!o.multiple, w, !1) : f !== !!o.multiple && (o.defaultValue != null ? Gt(
                l,
                !!o.multiple,
                o.defaultValue,
                !0
              ) : Gt(l, !!o.multiple, o.multiple ? [] : "", !1));
          }
          l[Kn] = o;
        } catch (E) {
          Y(e, e.return, E);
        }
      }
      break;
    case 6:
      if (Re(t, e), Ae(e), r & 4) {
        if (e.stateNode === null) throw Error(S(162));
        l = e.stateNode, o = e.memoizedProps;
        try {
          l.nodeValue = o;
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
      Re(t, e), Ae(e), l = e.child, l.flags & 8192 && (o = l.memoizedState !== null, l.stateNode.isHidden = o, !o || l.alternate !== null && l.alternate.memoizedState !== null || (Mi = G())), r & 4 && Qu(e);
      break;
    case 22:
      if (g = n !== null && n.memoizedState !== null, e.mode & 1 ? (ue = (c = ue) || g, Re(t, e), ue = c) : Re(t, e), Ae(e), r & 8192) {
        if (c = e.memoizedState !== null, (e.stateNode.isHidden = c) && !g && e.mode & 1) for (L = e, g = e.child; g !== null; ) {
          for (h = L = g; L !== null; ) {
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
                  Ku(h);
                  continue;
                }
            }
            w !== null ? (w.return = f, L = w) : Ku(h);
          }
          g = g.sibling;
        }
        e: for (g = null, h = e; ; ) {
          if (h.tag === 5) {
            if (g === null) {
              g = h;
              try {
                l = h.stateNode, c ? (o = l.style, typeof o.setProperty == "function" ? o.setProperty("display", "none", "important") : o.display = "none") : (u = h.stateNode, s = h.memoizedProps.style, i = s != null && s.hasOwnProperty("display") ? s.display : null, u.style.display = zs("display", i));
              } catch (E) {
                Y(e, e.return, E);
              }
            }
          } else if (h.tag === 6) {
            if (g === null) try {
              h.stateNode.nodeValue = c ? "" : h.memoizedProps;
            } catch (E) {
              Y(e, e.return, E);
            }
          } else if ((h.tag !== 22 && h.tag !== 23 || h.memoizedState === null || h === e) && h.child !== null) {
            h.child.return = h, h = h.child;
            continue;
          }
          if (h === e) break e;
          for (; h.sibling === null; ) {
            if (h.return === null || h.return === e) break e;
            g === h && (g = null), h = h.return;
          }
          g === h && (g = null), h.sibling.return = h.return, h = h.sibling;
        }
      }
      break;
    case 19:
      Re(t, e), Ae(e), r & 4 && Qu(e);
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
          if (rc(n)) {
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
          var o = Hu(e);
          Ho(e, o, l);
          break;
        case 3:
        case 4:
          var i = r.stateNode.containerInfo, u = Hu(e);
          Bo(e, u, i);
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
function Dd(e, t, n) {
  L = e, ic(e);
}
function ic(e, t, n) {
  for (var r = (e.mode & 1) !== 0; L !== null; ) {
    var l = L, o = l.child;
    if (l.tag === 22 && r) {
      var i = l.memoizedState !== null || xr;
      if (!i) {
        var u = l.alternate, s = u !== null && u.memoizedState !== null || ue;
        u = xr;
        var c = ue;
        if (xr = i, (ue = s) && !c) for (L = l; L !== null; ) i = L, s = i.child, i.tag === 22 && i.memoizedState !== null ? Yu(l) : s !== null ? (s.return = i, L = s) : Yu(l);
        for (; o !== null; ) L = o, ic(o), o = o.sibling;
        L = l, xr = u, ue = c;
      }
      Wu(e);
    } else l.subtreeFlags & 8772 && o !== null ? (o.return = l, L = o) : Wu(e);
  }
}
function Wu(e) {
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
            var o = t.updateQueue;
            o !== null && Lu(t, o, r);
            break;
          case 3:
            var i = t.updateQueue;
            if (i !== null) {
              if (n = null, t.child !== null) switch (t.child.tag) {
                case 5:
                  n = t.child.stateNode;
                  break;
                case 1:
                  n = t.child.stateNode;
              }
              Lu(t, i, n);
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
                var g = c.memoizedState;
                if (g !== null) {
                  var h = g.dehydrated;
                  h !== null && $n(h);
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
        ue || t.flags & 512 && $o(t);
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
function Ku(e) {
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
function Yu(e) {
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
          var o = t.return;
          try {
            $o(t);
          } catch (s) {
            Y(t, o, s);
          }
          break;
        case 5:
          var i = t.return;
          try {
            $o(t);
          } catch (s) {
            Y(t, i, s);
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
var Od = Math.ceil, nl = be.ReactCurrentDispatcher, zi = be.ReactCurrentOwner, Le = be.ReactCurrentBatchConfig, D = 0, te = null, Z = null, re = 0, we = 0, Xt = yt(0), q = 0, qn = null, Rt = 0, gl = 0, Ri = 0, In = null, me = null, Mi = 0, sn = 1 / 0, Qe = null, rl = !1, Qo = null, dt = null, Er = !1, it = null, ll = 0, Dn = 0, Wo = null, Ir = -1, Dr = 0;
function fe() {
  return D & 6 ? G() : Ir !== -1 ? Ir : Ir = G();
}
function pt(e) {
  return e.mode & 1 ? D & 2 && re !== 0 ? re & -re : wd.transition !== null ? (Dr === 0 && (Dr = Qs()), Dr) : (e = F, e !== 0 || (e = window.event, e = e === void 0 ? 16 : Js(e.type)), e) : 1;
}
function Fe(e, t, n, r) {
  if (50 < Dn) throw Dn = 0, Wo = null, Error(S(185));
  tr(e, n, r), (!(D & 2) || e !== te) && (e === te && (!(D & 2) && (gl |= n), q === 4 && lt(e, re)), ye(e, r), n === 1 && D === 0 && !(t.mode & 1) && (sn = G() + 500, pl && wt()));
}
function ye(e, t) {
  var n = e.callbackNode;
  wf(e, t);
  var r = $r(e, e === te ? re : 0);
  if (r === 0) n !== null && nu(n), e.callbackNode = null, e.callbackPriority = 0;
  else if (t = r & -r, e.callbackPriority !== t) {
    if (n != null && nu(n), t === 1) e.tag === 0 ? yd(Xu.bind(null, e)) : va(Xu.bind(null, e)), md(function() {
      !(D & 6) && wt();
    }), n = null;
    else {
      switch (Ws(r)) {
        case 1:
          n = oi;
          break;
        case 4:
          n = Bs;
          break;
        case 16:
          n = Vr;
          break;
        case 536870912:
          n = Hs;
          break;
        default:
          n = Vr;
      }
      n = mc(n, uc.bind(null, e));
    }
    e.callbackPriority = t, e.callbackNode = n;
  }
}
function uc(e, t) {
  if (Ir = -1, Dr = 0, D & 6) throw Error(S(327));
  var n = e.callbackNode;
  if (en() && e.callbackNode !== n) return null;
  var r = $r(e, e === te ? re : 0);
  if (r === 0) return null;
  if (r & 30 || r & e.expiredLanes || t) t = ol(e, r);
  else {
    t = r;
    var l = D;
    D |= 2;
    var o = ac();
    (te !== e || re !== t) && (Qe = null, sn = G() + 500, Pt(e, t));
    do
      try {
        Ad();
        break;
      } catch (u) {
        sc(e, u);
      }
    while (!0);
    yi(), nl.current = o, D = l, Z !== null ? t = 0 : (te = null, re = 0, t = q);
  }
  if (t !== 0) {
    if (t === 2 && (l = yo(e), l !== 0 && (r = l, t = Ko(e, l))), t === 1) throw n = qn, Pt(e, 0), lt(e, r), ye(e, G()), n;
    if (t === 6) lt(e, r);
    else {
      if (l = e.current.alternate, !(r & 30) && !Fd(l) && (t = ol(e, r), t === 2 && (o = yo(e), o !== 0 && (r = o, t = Ko(e, o))), t === 1)) throw n = qn, Pt(e, 0), lt(e, r), ye(e, G()), n;
      switch (e.finishedWork = l, e.finishedLanes = r, t) {
        case 0:
        case 1:
          throw Error(S(345));
        case 2:
          Et(e, me, Qe);
          break;
        case 3:
          if (lt(e, r), (r & 130023424) === r && (t = Mi + 500 - G(), 10 < t)) {
            if ($r(e, 0) !== 0) break;
            if (l = e.suspendedLanes, (l & r) !== r) {
              fe(), e.pingedLanes |= e.suspendedLanes & l;
              break;
            }
            e.timeoutHandle = No(Et.bind(null, e, me, Qe), t);
            break;
          }
          Et(e, me, Qe);
          break;
        case 4:
          if (lt(e, r), (r & 4194240) === r) break;
          for (t = e.eventTimes, l = -1; 0 < r; ) {
            var i = 31 - Oe(r);
            o = 1 << i, i = t[i], i > l && (l = i), r &= ~o;
          }
          if (r = l, r = G() - r, r = (120 > r ? 120 : 480 > r ? 480 : 1080 > r ? 1080 : 1920 > r ? 1920 : 3e3 > r ? 3e3 : 4320 > r ? 4320 : 1960 * Od(r / 1960)) - r, 10 < r) {
            e.timeoutHandle = No(Et.bind(null, e, me, Qe), r);
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
  return ye(e, G()), e.callbackNode === n ? uc.bind(null, e) : null;
}
function Ko(e, t) {
  var n = In;
  return e.current.memoizedState.isDehydrated && (Pt(e, t).flags |= 256), e = ol(e, t), e !== 2 && (t = me, me = n, t !== null && Yo(t)), e;
}
function Yo(e) {
  me === null ? me = e : me.push.apply(me, e);
}
function Fd(e) {
  for (var t = e; ; ) {
    if (t.flags & 16384) {
      var n = t.updateQueue;
      if (n !== null && (n = n.stores, n !== null)) for (var r = 0; r < n.length; r++) {
        var l = n[r], o = l.getSnapshot;
        l = l.value;
        try {
          if (!Ue(o(), l)) return !1;
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
  for (t &= ~Ri, t &= ~gl, e.suspendedLanes |= t, e.pingedLanes &= ~t, e = e.expirationTimes; 0 < t; ) {
    var n = 31 - Oe(t), r = 1 << n;
    e[n] = -1, t &= ~r;
  }
}
function Xu(e) {
  if (D & 6) throw Error(S(327));
  en();
  var t = $r(e, 0);
  if (!(t & 1)) return ye(e, G()), null;
  var n = ol(e, t);
  if (e.tag !== 0 && n === 2) {
    var r = yo(e);
    r !== 0 && (t = r, n = Ko(e, r));
  }
  if (n === 1) throw n = qn, Pt(e, 0), lt(e, t), ye(e, G()), n;
  if (n === 6) throw Error(S(345));
  return e.finishedWork = e.current.alternate, e.finishedLanes = t, Et(e, me, Qe), ye(e, G()), null;
}
function Ii(e, t) {
  var n = D;
  D |= 1;
  try {
    return e(t);
  } finally {
    D = n, D === 0 && (sn = G() + 500, pl && wt());
  }
}
function Mt(e) {
  it !== null && it.tag === 0 && !(D & 6) && en();
  var t = D;
  D |= 1;
  var n = Le.transition, r = F;
  try {
    if (Le.transition = null, F = 1, e) return e();
  } finally {
    F = r, Le.transition = n, D = t, !(D & 6) && wt();
  }
}
function Di() {
  we = Xt.current, $(Xt);
}
function Pt(e, t) {
  e.finishedWork = null, e.finishedLanes = 0;
  var n = e.timeoutHandle;
  if (n !== -1 && (e.timeoutHandle = -1, pd(n)), Z !== null) for (n = Z.return; n !== null; ) {
    var r = n;
    switch (hi(r), r.tag) {
      case 1:
        r = r.type.childContextTypes, r != null && Kr();
        break;
      case 3:
        on(), $(ve), $(se), Ci();
        break;
      case 5:
        Ei(r);
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
        wi(r.type._context);
        break;
      case 22:
      case 23:
        Di();
    }
    n = n.return;
  }
  if (te = e, Z = e = mt(e.current, null), re = we = t, q = 0, qn = null, Ri = gl = Rt = 0, me = In = null, _t !== null) {
    for (t = 0; t < _t.length; t++) if (n = _t[t], r = n.interleaved, r !== null) {
      n.interleaved = null;
      var l = r.next, o = n.pending;
      if (o !== null) {
        var i = o.next;
        o.next = l, r.next = i;
      }
      n.pending = r;
    }
    _t = null;
  }
  return e;
}
function sc(e, t) {
  do {
    var n = Z;
    try {
      if (yi(), zr.current = tl, el) {
        for (var r = W.memoizedState; r !== null; ) {
          var l = r.queue;
          l !== null && (l.pending = null), r = r.next;
        }
        el = !1;
      }
      if (zt = 0, ee = J = W = null, Rn = !1, Gn = 0, zi.current = null, n === null || n.return === null) {
        q = 1, qn = t, Z = null;
        break;
      }
      e: {
        var o = e, i = n.return, u = n, s = t;
        if (t = re, u.flags |= 32768, s !== null && typeof s == "object" && typeof s.then == "function") {
          var c = s, g = u, h = g.tag;
          if (!(g.mode & 1) && (h === 0 || h === 11 || h === 15)) {
            var f = g.alternate;
            f ? (g.updateQueue = f.updateQueue, g.memoizedState = f.memoizedState, g.lanes = f.lanes) : (g.updateQueue = null, g.memoizedState = null);
          }
          var w = Du(i);
          if (w !== null) {
            w.flags &= -257, Ou(w, i, u, o, t), w.mode & 1 && Iu(o, c, t), t = w, s = c;
            var k = t.updateQueue;
            if (k === null) {
              var E = /* @__PURE__ */ new Set();
              E.add(s), t.updateQueue = E;
            } else k.add(s);
            break e;
          } else {
            if (!(t & 1)) {
              Iu(o, c, t), Oi();
              break e;
            }
            s = Error(S(426));
          }
        } else if (B && u.mode & 1) {
          var z = Du(i);
          if (z !== null) {
            !(z.flags & 65536) && (z.flags |= 256), Ou(z, i, u, o, t), vi(un(s, u));
            break e;
          }
        }
        o = s = un(s, u), q !== 4 && (q = 2), In === null ? In = [o] : In.push(o), o = i;
        do {
          switch (o.tag) {
            case 3:
              o.flags |= 65536, t &= -t, o.lanes |= t;
              var d = Wa(o, s, t);
              ju(o, d);
              break e;
            case 1:
              u = s;
              var a = o.type, p = o.stateNode;
              if (!(o.flags & 128) && (typeof a.getDerivedStateFromError == "function" || p !== null && typeof p.componentDidCatch == "function" && (dt === null || !dt.has(p)))) {
                o.flags |= 65536, t &= -t, o.lanes |= t;
                var v = Ka(o, u, t);
                ju(o, v);
                break e;
              }
          }
          o = o.return;
        } while (o !== null);
      }
      fc(n);
    } catch (C) {
      t = C, Z === n && n !== null && (Z = n = n.return);
      continue;
    }
    break;
  } while (!0);
}
function ac() {
  var e = nl.current;
  return nl.current = tl, e === null ? tl : e;
}
function Oi() {
  (q === 0 || q === 3 || q === 2) && (q = 4), te === null || !(Rt & 268435455) && !(gl & 268435455) || lt(te, re);
}
function ol(e, t) {
  var n = D;
  D |= 2;
  var r = ac();
  (te !== e || re !== t) && (Qe = null, Pt(e, t));
  do
    try {
      Ud();
      break;
    } catch (l) {
      sc(e, l);
    }
  while (!0);
  if (yi(), D = n, nl.current = r, Z !== null) throw Error(S(261));
  return te = null, re = 0, q;
}
function Ud() {
  for (; Z !== null; ) cc(Z);
}
function Ad() {
  for (; Z !== null && !cf(); ) cc(Z);
}
function cc(e) {
  var t = pc(e.alternate, e, we);
  e.memoizedProps = e.pendingProps, t === null ? fc(e) : Z = t, zi.current = null;
}
function fc(e) {
  var t = e;
  do {
    var n = t.alternate;
    if (e = t.return, t.flags & 32768) {
      if (n = Rd(n, t), n !== null) {
        n.flags &= 32767, Z = n;
        return;
      }
      if (e !== null) e.flags |= 32768, e.subtreeFlags = 0, e.deletions = null;
      else {
        q = 6, Z = null;
        return;
      }
    } else if (n = zd(n, t, we), n !== null) {
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
    Le.transition = null, F = 1, Vd(e, t, n, r);
  } finally {
    Le.transition = l, F = r;
  }
  return null;
}
function Vd(e, t, n, r) {
  do
    en();
  while (it !== null);
  if (D & 6) throw Error(S(327));
  n = e.finishedWork;
  var l = e.finishedLanes;
  if (n === null) return null;
  if (e.finishedWork = null, e.finishedLanes = 0, n === e.current) throw Error(S(177));
  e.callbackNode = null, e.callbackPriority = 0;
  var o = n.lanes | n.childLanes;
  if (kf(e, o), e === te && (Z = te = null, re = 0), !(n.subtreeFlags & 2064) && !(n.flags & 2064) || Er || (Er = !0, mc(Vr, function() {
    return en(), null;
  })), o = (n.flags & 15990) !== 0, n.subtreeFlags & 15990 || o) {
    o = Le.transition, Le.transition = null;
    var i = F;
    F = 1;
    var u = D;
    D |= 4, zi.current = null, Id(e, n), oc(n, e), id(Co), Br = !!Eo, Co = Eo = null, e.current = n, Dd(n), ff(), D = u, F = i, Le.transition = o;
  } else e.current = n;
  if (Er && (Er = !1, it = e, ll = l), o = e.pendingLanes, o === 0 && (dt = null), mf(n.stateNode), ye(e, G()), t !== null) for (r = e.onRecoverableError, n = 0; n < t.length; n++) l = t[n], r(l.value, { componentStack: l.stack, digest: l.digest });
  if (rl) throw rl = !1, e = Qo, Qo = null, e;
  return ll & 1 && e.tag !== 0 && en(), o = e.pendingLanes, o & 1 ? e === Wo ? Dn++ : (Dn = 0, Wo = e) : Dn = 0, wt(), null;
}
function en() {
  if (it !== null) {
    var e = Ws(ll), t = Le.transition, n = F;
    try {
      if (Le.transition = null, F = 16 > e ? 16 : e, it === null) var r = !1;
      else {
        if (e = it, it = null, ll = 0, D & 6) throw Error(S(331));
        var l = D;
        for (D |= 4, L = e.current; L !== null; ) {
          var o = L, i = o.child;
          if (L.flags & 16) {
            var u = o.deletions;
            if (u !== null) {
              for (var s = 0; s < u.length; s++) {
                var c = u[s];
                for (L = c; L !== null; ) {
                  var g = L;
                  switch (g.tag) {
                    case 0:
                    case 11:
                    case 15:
                      Mn(8, g, o);
                  }
                  var h = g.child;
                  if (h !== null) h.return = g, L = h;
                  else for (; L !== null; ) {
                    g = L;
                    var f = g.sibling, w = g.return;
                    if (nc(g), g === c) {
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
              var k = o.alternate;
              if (k !== null) {
                var E = k.child;
                if (E !== null) {
                  k.child = null;
                  do {
                    var z = E.sibling;
                    E.sibling = null, E = z;
                  } while (E !== null);
                }
              }
              L = o;
            }
          }
          if (o.subtreeFlags & 2064 && i !== null) i.return = o, L = i;
          else e: for (; L !== null; ) {
            if (o = L, o.flags & 2048) switch (o.tag) {
              case 0:
              case 11:
              case 15:
                Mn(9, o, o.return);
            }
            var d = o.sibling;
            if (d !== null) {
              d.return = o.return, L = d;
              break e;
            }
            L = o.return;
          }
        }
        var a = e.current;
        for (L = a; L !== null; ) {
          i = L;
          var p = i.child;
          if (i.subtreeFlags & 2064 && p !== null) p.return = i, L = p;
          else e: for (i = a; L !== null; ) {
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
            if (u === i) {
              L = null;
              break e;
            }
            var v = u.sibling;
            if (v !== null) {
              v.return = u.return, L = v;
              break e;
            }
            L = u.return;
          }
        }
        if (D = l, wt(), Be && typeof Be.onPostCommitFiberRoot == "function") try {
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
function Gu(e, t, n) {
  t = un(n, t), t = Wa(e, t, 1), e = ft(e, t, 1), t = fe(), e !== null && (tr(e, 1, t), ye(e, t));
}
function Y(e, t, n) {
  if (e.tag === 3) Gu(e, e, n);
  else for (; t !== null; ) {
    if (t.tag === 3) {
      Gu(t, e, n);
      break;
    } else if (t.tag === 1) {
      var r = t.stateNode;
      if (typeof t.type.getDerivedStateFromError == "function" || typeof r.componentDidCatch == "function" && (dt === null || !dt.has(r))) {
        e = un(n, e), e = Ka(t, e, 1), t = ft(t, e, 1), e = fe(), t !== null && (tr(t, 1, e), ye(t, e));
        break;
      }
    }
    t = t.return;
  }
}
function $d(e, t, n) {
  var r = e.pingCache;
  r !== null && r.delete(t), t = fe(), e.pingedLanes |= e.suspendedLanes & n, te === e && (re & n) === n && (q === 4 || q === 3 && (re & 130023424) === re && 500 > G() - Mi ? Pt(e, 0) : Ri |= n), ye(e, t);
}
function dc(e, t) {
  t === 0 && (e.mode & 1 ? (t = pr, pr <<= 1, !(pr & 130023424) && (pr = 4194304)) : t = 1);
  var n = fe();
  e = Je(e, t), e !== null && (tr(e, t, n), ye(e, n));
}
function Bd(e) {
  var t = e.memoizedState, n = 0;
  t !== null && (n = t.retryLane), dc(e, n);
}
function Hd(e, t) {
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
  r !== null && r.delete(t), dc(e, n);
}
var pc;
pc = function(e, t, n) {
  if (e !== null) if (e.memoizedProps !== t.pendingProps || ve.current) he = !0;
  else {
    if (!(e.lanes & n) && !(t.flags & 128)) return he = !1, Td(e, t, n);
    he = !!(e.flags & 131072);
  }
  else he = !1, B && t.flags & 1048576 && ga(t, Gr, t.index);
  switch (t.lanes = 0, t.tag) {
    case 2:
      var r = t.type;
      Mr(e, t), e = t.pendingProps;
      var l = nn(t, se.current);
      bt(t, n), l = Ni(null, t, r, e, l, n);
      var o = Pi();
      return t.flags |= 1, typeof l == "object" && l !== null && typeof l.render == "function" && l.$$typeof === void 0 ? (t.tag = 1, t.memoizedState = null, t.updateQueue = null, ge(r) ? (o = !0, Yr(t)) : o = !1, t.memoizedState = l.state !== null && l.state !== void 0 ? l.state : null, Si(t), l.updater = hl, t.stateNode = l, l._reactInternals = t, Mo(t, r, e, n), t = Oo(null, t, r, !0, o, n)) : (t.tag = 0, B && o && mi(t), ce(null, t, l, n), t = t.child), t;
    case 16:
      r = t.elementType;
      e: {
        switch (Mr(e, t), e = t.pendingProps, l = r._init, r = l(r._payload), t.type = r, l = t.tag = Wd(r), e = Me(r, e), l) {
          case 0:
            t = Do(null, t, r, e, n);
            break e;
          case 1:
            t = Au(null, t, r, e, n);
            break e;
          case 11:
            t = Fu(null, t, r, e, n);
            break e;
          case 14:
            t = Uu(null, t, r, Me(r.type, e), n);
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
      return r = t.type, l = t.pendingProps, l = t.elementType === r ? l : Me(r, l), Do(e, t, r, l, n);
    case 1:
      return r = t.type, l = t.pendingProps, l = t.elementType === r ? l : Me(r, l), Au(e, t, r, l, n);
    case 3:
      e: {
        if (Za(t), e === null) throw Error(S(387));
        r = t.pendingProps, o = t.memoizedState, l = o.element, Ea(e, t), qr(t, r, null, n);
        var i = t.memoizedState;
        if (r = i.element, o.isDehydrated) if (o = { element: r, isDehydrated: !1, cache: i.cache, pendingSuspenseBoundaries: i.pendingSuspenseBoundaries, transitions: i.transitions }, t.updateQueue.baseState = o, t.memoizedState = o, t.flags & 256) {
          l = un(Error(S(423)), t), t = Vu(e, t, r, n, l);
          break e;
        } else if (r !== l) {
          l = un(Error(S(424)), t), t = Vu(e, t, r, n, l);
          break e;
        } else for (ke = ct(t.stateNode.containerInfo.firstChild), Se = t, B = !0, De = null, n = Sa(t, null, r, n), t.child = n; n; ) n.flags = n.flags & -3 | 4096, n = n.sibling;
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
      return Ca(t), e === null && To(t), r = t.type, l = t.pendingProps, o = e !== null ? e.memoizedProps : null, i = l.children, _o(r, l) ? i = null : o !== null && _o(r, o) && (t.flags |= 32), Ga(e, t), ce(e, t, i, n), t.child;
    case 6:
      return e === null && To(t), null;
    case 13:
      return Ja(e, t, n);
    case 4:
      return xi(t, t.stateNode.containerInfo), r = t.pendingProps, e === null ? t.child = ln(t, null, r, n) : ce(e, t, r, n), t.child;
    case 11:
      return r = t.type, l = t.pendingProps, l = t.elementType === r ? l : Me(r, l), Fu(e, t, r, l, n);
    case 7:
      return ce(e, t, t.pendingProps, n), t.child;
    case 8:
      return ce(e, t, t.pendingProps.children, n), t.child;
    case 12:
      return ce(e, t, t.pendingProps.children, n), t.child;
    case 10:
      e: {
        if (r = t.type._context, l = t.pendingProps, o = t.memoizedProps, i = l.value, U(Zr, r._currentValue), r._currentValue = i, o !== null) if (Ue(o.value, i)) {
          if (o.children === l.children && !ve.current) {
            t = qe(e, t, n);
            break e;
          }
        } else for (o = t.child, o !== null && (o.return = t); o !== null; ) {
          var u = o.dependencies;
          if (u !== null) {
            i = o.child;
            for (var s = u.firstContext; s !== null; ) {
              if (s.context === r) {
                if (o.tag === 1) {
                  s = Xe(-1, n & -n), s.tag = 2;
                  var c = o.updateQueue;
                  if (c !== null) {
                    c = c.shared;
                    var g = c.pending;
                    g === null ? s.next = s : (s.next = g.next, g.next = s), c.pending = s;
                  }
                }
                o.lanes |= n, s = o.alternate, s !== null && (s.lanes |= n), zo(
                  o.return,
                  n,
                  t
                ), u.lanes |= n;
                break;
              }
              s = s.next;
            }
          } else if (o.tag === 10) i = o.type === t.type ? null : o.child;
          else if (o.tag === 18) {
            if (i = o.return, i === null) throw Error(S(341));
            i.lanes |= n, u = i.alternate, u !== null && (u.lanes |= n), zo(i, n, t), i = o.sibling;
          } else i = o.child;
          if (i !== null) i.return = o;
          else for (i = o; i !== null; ) {
            if (i === t) {
              i = null;
              break;
            }
            if (o = i.sibling, o !== null) {
              o.return = i.return, i = o;
              break;
            }
            i = i.return;
          }
          o = i;
        }
        ce(e, t, l.children, n), t = t.child;
      }
      return t;
    case 9:
      return l = t.type, r = t.pendingProps.children, bt(t, n), l = Te(l), r = r(l), t.flags |= 1, ce(e, t, r, n), t.child;
    case 14:
      return r = t.type, l = Me(r, t.pendingProps), l = Me(r.type, l), Uu(e, t, r, l, n);
    case 15:
      return Ya(e, t, t.type, t.pendingProps, n);
    case 17:
      return r = t.type, l = t.pendingProps, l = t.elementType === r ? l : Me(r, l), Mr(e, t), t.tag = 1, ge(r) ? (e = !0, Yr(t)) : e = !1, bt(t, n), Qa(t, r, l), Mo(t, r, l, n), Oo(null, t, r, !0, e, n);
    case 19:
      return qa(e, t, n);
    case 22:
      return Xa(e, t, n);
  }
  throw Error(S(156, t.tag));
};
function mc(e, t) {
  return $s(e, t);
}
function Qd(e, t, n, r) {
  this.tag = e, this.key = n, this.sibling = this.child = this.return = this.stateNode = this.type = this.elementType = null, this.index = 0, this.ref = null, this.pendingProps = t, this.dependencies = this.memoizedState = this.updateQueue = this.memoizedProps = null, this.mode = r, this.subtreeFlags = this.flags = 0, this.deletions = null, this.childLanes = this.lanes = 0, this.alternate = null;
}
function je(e, t, n, r) {
  return new Qd(e, t, n, r);
}
function Fi(e) {
  return e = e.prototype, !(!e || !e.isReactComponent);
}
function Wd(e) {
  if (typeof e == "function") return Fi(e) ? 1 : 0;
  if (e != null) {
    if (e = e.$$typeof, e === ni) return 11;
    if (e === ri) return 14;
  }
  return 2;
}
function mt(e, t) {
  var n = e.alternate;
  return n === null ? (n = je(e.tag, t, e.key, e.mode), n.elementType = e.elementType, n.type = e.type, n.stateNode = e.stateNode, n.alternate = e, e.alternate = n) : (n.pendingProps = t, n.type = e.type, n.flags = 0, n.subtreeFlags = 0, n.deletions = null), n.flags = e.flags & 14680064, n.childLanes = e.childLanes, n.lanes = e.lanes, n.child = e.child, n.memoizedProps = e.memoizedProps, n.memoizedState = e.memoizedState, n.updateQueue = e.updateQueue, t = e.dependencies, n.dependencies = t === null ? null : { lanes: t.lanes, firstContext: t.firstContext }, n.sibling = e.sibling, n.index = e.index, n.ref = e.ref, n;
}
function Or(e, t, n, r, l, o) {
  var i = 2;
  if (r = e, typeof e == "function") Fi(e) && (i = 1);
  else if (typeof e == "string") i = 5;
  else e: switch (e) {
    case Ut:
      return jt(n.children, l, o, t);
    case ti:
      i = 8, l |= 8;
      break;
    case no:
      return e = je(12, n, t, l | 2), e.elementType = no, e.lanes = o, e;
    case ro:
      return e = je(13, n, t, l), e.elementType = ro, e.lanes = o, e;
    case lo:
      return e = je(19, n, t, l), e.elementType = lo, e.lanes = o, e;
    case Cs:
      return yl(n, l, o, t);
    default:
      if (typeof e == "object" && e !== null) switch (e.$$typeof) {
        case xs:
          i = 10;
          break e;
        case Es:
          i = 9;
          break e;
        case ni:
          i = 11;
          break e;
        case ri:
          i = 14;
          break e;
        case tt:
          i = 16, r = null;
          break e;
      }
      throw Error(S(130, e == null ? e : typeof e, ""));
  }
  return t = je(i, n, t, l), t.elementType = e, t.type = r, t.lanes = o, t;
}
function jt(e, t, n, r) {
  return e = je(7, e, r, t), e.lanes = n, e;
}
function yl(e, t, n, r) {
  return e = je(22, e, r, t), e.elementType = Cs, e.lanes = n, e.stateNode = { isHidden: !1 }, e;
}
function Gl(e, t, n) {
  return e = je(6, e, null, t), e.lanes = n, e;
}
function Zl(e, t, n) {
  return t = je(4, e.children !== null ? e.children : [], e.key, t), t.lanes = n, t.stateNode = { containerInfo: e.containerInfo, pendingChildren: null, implementation: e.implementation }, t;
}
function Kd(e, t, n, r, l) {
  this.tag = t, this.containerInfo = e, this.finishedWork = this.pingCache = this.current = this.pendingChildren = null, this.timeoutHandle = -1, this.callbackNode = this.pendingContext = this.context = null, this.callbackPriority = 0, this.eventTimes = Tl(0), this.expirationTimes = Tl(-1), this.entangledLanes = this.finishedLanes = this.mutableReadLanes = this.expiredLanes = this.pingedLanes = this.suspendedLanes = this.pendingLanes = 0, this.entanglements = Tl(0), this.identifierPrefix = r, this.onRecoverableError = l, this.mutableSourceEagerHydrationData = null;
}
function Ui(e, t, n, r, l, o, i, u, s) {
  return e = new Kd(e, t, n, u, s), t === 1 ? (t = 1, o === !0 && (t |= 8)) : t = 0, o = je(3, null, null, t), e.current = o, o.stateNode = e, o.memoizedState = { element: r, isDehydrated: n, cache: null, transitions: null, pendingSuspenseBoundaries: null }, Si(o), e;
}
function Yd(e, t, n) {
  var r = 3 < arguments.length && arguments[3] !== void 0 ? arguments[3] : null;
  return { $$typeof: Ft, key: r == null ? null : "" + r, children: e, containerInfo: t, implementation: n };
}
function hc(e) {
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
    if (ge(n)) return ha(e, n, t);
  }
  return t;
}
function vc(e, t, n, r, l, o, i, u, s) {
  return e = Ui(n, r, !0, e, l, o, i, u, s), e.context = hc(null), n = e.current, r = fe(), l = pt(n), o = Xe(r, l), o.callback = t ?? null, ft(n, o, l), e.current.lanes = l, tr(e, l, r), ye(e, r), e;
}
function wl(e, t, n, r) {
  var l = t.current, o = fe(), i = pt(l);
  return n = hc(n), t.context === null ? t.context = n : t.pendingContext = n, t = Xe(o, i), t.payload = { element: e }, r = r === void 0 ? null : r, r !== null && (t.callback = r), e = ft(l, t, i), e !== null && (Fe(e, l, i, o), Tr(e, l, i)), i;
}
function il(e) {
  if (e = e.current, !e.child) return null;
  switch (e.child.tag) {
    case 5:
      return e.child.stateNode;
    default:
      return e.child.stateNode;
  }
}
function Zu(e, t) {
  if (e = e.memoizedState, e !== null && e.dehydrated !== null) {
    var n = e.retryLane;
    e.retryLane = n !== 0 && n < t ? n : t;
  }
}
function Ai(e, t) {
  Zu(e, t), (e = e.alternate) && Zu(e, t);
}
function Xd() {
  return null;
}
var gc = typeof reportError == "function" ? reportError : function(e) {
  console.error(e);
};
function Vi(e) {
  this._internalRoot = e;
}
kl.prototype.render = Vi.prototype.render = function(e) {
  var t = this._internalRoot;
  if (t === null) throw Error(S(409));
  wl(e, t, null, null);
};
kl.prototype.unmount = Vi.prototype.unmount = function() {
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
    var t = Xs();
    e = { blockedOn: null, target: e, priority: t };
    for (var n = 0; n < rt.length && t !== 0 && t < rt[n].priority; n++) ;
    rt.splice(n, 0, e), n === 0 && Zs(e);
  }
};
function $i(e) {
  return !(!e || e.nodeType !== 1 && e.nodeType !== 9 && e.nodeType !== 11);
}
function Sl(e) {
  return !(!e || e.nodeType !== 1 && e.nodeType !== 9 && e.nodeType !== 11 && (e.nodeType !== 8 || e.nodeValue !== " react-mount-point-unstable "));
}
function Ju() {
}
function Gd(e, t, n, r, l) {
  if (l) {
    if (typeof r == "function") {
      var o = r;
      r = function() {
        var c = il(i);
        o.call(c);
      };
    }
    var i = vc(t, r, e, 0, null, !1, !1, "", Ju);
    return e._reactRootContainer = i, e[Ze] = i.current, Qn(e.nodeType === 8 ? e.parentNode : e), Mt(), i;
  }
  for (; l = e.lastChild; ) e.removeChild(l);
  if (typeof r == "function") {
    var u = r;
    r = function() {
      var c = il(s);
      u.call(c);
    };
  }
  var s = Ui(e, 0, !1, null, null, !1, !1, "", Ju);
  return e._reactRootContainer = s, e[Ze] = s.current, Qn(e.nodeType === 8 ? e.parentNode : e), Mt(function() {
    wl(t, s, n, r);
  }), s;
}
function xl(e, t, n, r, l) {
  var o = n._reactRootContainer;
  if (o) {
    var i = o;
    if (typeof l == "function") {
      var u = l;
      l = function() {
        var s = il(i);
        u.call(s);
      };
    }
    wl(t, i, e, l);
  } else i = Gd(n, t, e, l, r);
  return il(i);
}
Ks = function(e) {
  switch (e.tag) {
    case 3:
      var t = e.stateNode;
      if (t.current.memoizedState.isDehydrated) {
        var n = _n(t.pendingLanes);
        n !== 0 && (ii(t, n | 1), ye(t, G()), !(D & 6) && (sn = G() + 500, wt()));
      }
      break;
    case 13:
      Mt(function() {
        var r = Je(e, 1);
        if (r !== null) {
          var l = fe();
          Fe(r, e, 1, l);
        }
      }), Ai(e, 1);
  }
};
ui = function(e) {
  if (e.tag === 13) {
    var t = Je(e, 134217728);
    if (t !== null) {
      var n = fe();
      Fe(t, e, 134217728, n);
    }
    Ai(e, 134217728);
  }
};
Ys = function(e) {
  if (e.tag === 13) {
    var t = pt(e), n = Je(e, t);
    if (n !== null) {
      var r = fe();
      Fe(n, e, t, r);
    }
    Ai(e, t);
  }
};
Xs = function() {
  return F;
};
Gs = function(e, t) {
  var n = F;
  try {
    return F = e, t();
  } finally {
    F = n;
  }
};
ho = function(e, t, n) {
  switch (t) {
    case "input":
      if (uo(e, n), t = n.name, n.type === "radio" && t != null) {
        for (n = e; n.parentNode; ) n = n.parentNode;
        for (n = n.querySelectorAll("input[name=" + JSON.stringify("" + t) + '][type="radio"]'), t = 0; t < n.length; t++) {
          var r = n[t];
          if (r !== e && r.form === e.form) {
            var l = dl(r);
            if (!l) throw Error(S(90));
            Ns(r), uo(r, l);
          }
        }
      }
      break;
    case "textarea":
      js(e, n);
      break;
    case "select":
      t = n.value, t != null && Gt(e, !!n.multiple, t, !1);
  }
};
Ds = Ii;
Os = Mt;
var Zd = { usingClientEntryPoint: !1, Events: [rr, Bt, dl, Ms, Is, Ii] }, xn = { findFiberByHostInstance: Ct, bundleType: 0, version: "18.3.1", rendererPackageName: "react-dom" }, Jd = { bundleType: xn.bundleType, version: xn.version, rendererPackageName: xn.rendererPackageName, rendererConfig: xn.rendererConfig, overrideHookState: null, overrideHookStateDeletePath: null, overrideHookStateRenamePath: null, overrideProps: null, overridePropsDeletePath: null, overridePropsRenamePath: null, setErrorHandler: null, setSuspenseHandler: null, scheduleUpdate: null, currentDispatcherRef: be.ReactCurrentDispatcher, findHostInstanceByFiber: function(e) {
  return e = As(e), e === null ? null : e.stateNode;
}, findFiberByHostInstance: xn.findFiberByHostInstance || Xd, findHostInstancesForRefresh: null, scheduleRefresh: null, scheduleRoot: null, setRefreshHandler: null, getCurrentFiber: null, reconcilerVersion: "18.3.1-next-f1338f8080-20240426" };
if (typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ < "u") {
  var Cr = __REACT_DEVTOOLS_GLOBAL_HOOK__;
  if (!Cr.isDisabled && Cr.supportsFiber) try {
    sl = Cr.inject(Jd), Be = Cr;
  } catch {
  }
}
Ee.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED = Zd;
Ee.createPortal = function(e, t) {
  var n = 2 < arguments.length && arguments[2] !== void 0 ? arguments[2] : null;
  if (!$i(t)) throw Error(S(200));
  return Yd(e, t, null, n);
};
Ee.createRoot = function(e, t) {
  if (!$i(e)) throw Error(S(299));
  var n = !1, r = "", l = gc;
  return t != null && (t.unstable_strictMode === !0 && (n = !0), t.identifierPrefix !== void 0 && (r = t.identifierPrefix), t.onRecoverableError !== void 0 && (l = t.onRecoverableError)), t = Ui(e, 1, !1, null, null, n, !1, r, l), e[Ze] = t.current, Qn(e.nodeType === 8 ? e.parentNode : e), new Vi(t);
};
Ee.findDOMNode = function(e) {
  if (e == null) return null;
  if (e.nodeType === 1) return e;
  var t = e._reactInternals;
  if (t === void 0)
    throw typeof e.render == "function" ? Error(S(188)) : (e = Object.keys(e).join(","), Error(S(268, e)));
  return e = As(t), e = e === null ? null : e.stateNode, e;
};
Ee.flushSync = function(e) {
  return Mt(e);
};
Ee.hydrate = function(e, t, n) {
  if (!Sl(t)) throw Error(S(200));
  return xl(null, e, t, !0, n);
};
Ee.hydrateRoot = function(e, t, n) {
  if (!$i(e)) throw Error(S(405));
  var r = n != null && n.hydratedSources || null, l = !1, o = "", i = gc;
  if (n != null && (n.unstable_strictMode === !0 && (l = !0), n.identifierPrefix !== void 0 && (o = n.identifierPrefix), n.onRecoverableError !== void 0 && (i = n.onRecoverableError)), t = vc(t, null, e, 1, n ?? null, l, !1, o, i), e[Ze] = t.current, Qn(e), r) for (e = 0; e < r.length; e++) n = r[e], l = n._getVersion, l = l(n._source), t.mutableSourceEagerHydrationData == null ? t.mutableSourceEagerHydrationData = [n, l] : t.mutableSourceEagerHydrationData.push(
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
Ee.unstable_batchedUpdates = Ii;
Ee.unstable_renderSubtreeIntoContainer = function(e, t, n, r) {
  if (!Sl(n)) throw Error(S(200));
  if (e == null || e._reactInternals === void 0) throw Error(S(38));
  return xl(e, t, n, !1, r);
};
Ee.version = "18.3.1-next-f1338f8080-20240426";
function yc() {
  if (!(typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ > "u" || typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE != "function"))
    try {
      __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE(yc);
    } catch (e) {
      console.error(e);
    }
}
yc(), ys.exports = Ee;
var wc = ys.exports, kc, qu = wc;
kc = qu.createRoot, qu.hydrateRoot;
function Jl(...e) {
  return e.filter(Boolean).join(" ");
}
function qd({
  content: e,
  children: t,
  className: n,
  triggerClassName: r,
  bubbleClassName: l,
  focusable: o = !1,
  ariaLabel: i,
  inline: u = !0
}) {
  const s = O.useRef(null), c = O.useRef(null), g = O.useRef(null), [h, f] = O.useState(!1), [w, k] = O.useState({}), [E, z] = O.useState("bottom"), d = O.useCallback(() => {
    const v = c.current, C = g.current;
    if (!v || !C || typeof window > "u")
      return;
    const _ = 12, x = 12, N = v.getBoundingClientRect(), I = C.getBoundingClientRect(), y = Math.ceil(I.height), P = Math.ceil(I.width), A = N.left + N.width / 2 - P / 2, ae = Math.min(
      Math.max(_, A),
      Math.max(_, window.innerWidth - P - _)
    ), or = N.bottom + x + y + _ <= window.innerHeight || N.top < y + x + _ ? "bottom" : "top", pn = or === "bottom" ? N.bottom + x : Math.max(_, N.top - y - x), mn = N.left + N.width / 2, j = Math.min(
      Math.max(18, mn - ae),
      Math.max(18, P - 18)
    );
    z(or), k({
      left: ae,
      top: pn,
      "--admin-tooltip-arrow-left": `${j}px`
    });
  }, []);
  O.useLayoutEffect(() => {
    if (!h)
      return;
    d();
    const v = () => d();
    return window.addEventListener("resize", v), window.addEventListener("scroll", v, !0), () => {
      window.removeEventListener("resize", v), window.removeEventListener("scroll", v, !0);
    };
  }, [d, h]);
  const a = u ? "span" : "div", p = u ? "span" : "div";
  return /* @__PURE__ */ m.jsxs(
    a,
    {
      className: Jl("admin-tooltip", n),
      onMouseEnter: () => f(!0),
      onMouseLeave: () => f(!1),
      onFocusCapture: () => f(!0),
      onBlurCapture: (v) => {
        var C;
        (C = s.current) != null && C.contains(v.relatedTarget) || f(!1);
      },
      ref: (v) => {
        s.current = v;
      },
      children: [
        /* @__PURE__ */ m.jsx(
          p,
          {
            className: Jl("admin-tooltip-trigger", r),
            ...o ? { tabIndex: 0, "aria-label": i } : {},
            ref: (v) => {
              c.current = v;
            },
            children: t
          }
        ),
        typeof document < "u" ? wc.createPortal(
          /* @__PURE__ */ m.jsx(
            "span",
            {
              ref: g,
              className: Jl("admin-tooltip-bubble", `placement-${E}`, l, h && "visible"),
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
function bd(e) {
  return typeof e == "object" && e !== null && !Array.isArray(e);
}
function ep(e) {
  var n;
  const t = typeof ((n = globalThis.crypto) == null ? void 0 : n.randomUUID) == "function" ? globalThis.crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${e}-${t}`;
}
function bn(e) {
  const t = /* @__PURE__ */ new Set(), n = [];
  for (const r of e) {
    const l = r.trim();
    !l || t.has(l) || (t.add(l), n.push(l));
  }
  return n;
}
function bu(e) {
  return bn(e).join(`
`);
}
function Sc(e = "", t = "") {
  return {
    id: ep("map-row"),
    key: e,
    value: t
  };
}
function es(e) {
  return bd(e) ? Object.entries(e).map(([t, n]) => Sc(t, String(n ?? ""))) : [];
}
function ts(e, t, n = !1) {
  const r = {}, l = /* @__PURE__ */ new Set();
  for (const o of e) {
    const i = o.key.trim(), u = o.value;
    if (!(!i && !u.trim())) {
      if (!i)
        throw new Error(`${t} contains a value without a key.`);
      if (l.has(i))
        throw new Error(`${t} contains duplicate key '${i}'.`);
      l.add(i), r[i] = u;
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
function ns(e, t) {
  const n = e.map((r) => t ? t(r) : r);
  return bn(n);
}
function Bi(e, t) {
  return /* @__PURE__ */ m.jsx("div", { className: "empty-state", children: e ?? t });
}
function xc(e, t, n) {
  if (e.current === n) return !1;
  const r = t.current === n;
  return e.current = n, r ? (t.current = null, !1) : (t.current = null, !0);
}
function rs({
  values: e,
  onChange: t,
  disabled: n = !1,
  className: r,
  description: l,
  emptyState: o,
  addLabel: i = "Add item",
  itemLabel: u = "Value",
  placeholder: s = "Enter a value",
  suggestions: c,
  validateItem: g,
  normalizeItem: h
}) {
  const f = O.useId(), w = ns(e, h), k = bu(w), [E, z] = O.useState(() => w.map((x) => ql(x))), d = O.useRef(k), a = O.useRef(null);
  O.useEffect(() => {
    xc(d, a, k) && z(w.map((x) => ql(x)));
  }, [w, k]);
  function p(x) {
    const N = ns(
      x.map((I) => I.value),
      h
    );
    a.current = bu(N), t(N);
  }
  const C = (c ?? []).map((x) => String(x)), _ = C.length > 0 ? bn(C) : [];
  return /* @__PURE__ */ m.jsxs("div", { className: ["string-list-selector", "structured-input-editor", r].filter(Boolean).join(" "), children: [
    l ? /* @__PURE__ */ m.jsx("div", { className: "structured-input-description", children: l }) : null,
    /* @__PURE__ */ m.jsx("div", { className: "inline-actions compact-actions", children: /* @__PURE__ */ m.jsx(
      "button",
      {
        type: "button",
        onClick: () => {
          const x = [...E, ql()];
          z(x);
        },
        disabled: n,
        children: i
      }
    ) }),
    E.length === 0 ? Bi(o, `No ${u.toLowerCase()}s are configured.`) : null,
    E.map((x, N) => {
      const I = (g == null ? void 0 : g(x.value, N, E.map((y) => y.value))) ?? null;
      return /* @__PURE__ */ m.jsxs("div", { className: "string-list-selector-row structured-input-row", children: [
        /* @__PURE__ */ m.jsxs("label", { className: "field-stack structured-input-field", children: [
          /* @__PURE__ */ m.jsx("span", { children: u }),
          /* @__PURE__ */ m.jsx(
            "input",
            {
              list: _.length > 0 ? f : void 0,
              value: x.value,
              onChange: (y) => {
                const P = h ? h(y.target.value) : y.target.value, A = E.map(
                  (ae, dn) => dn === N ? { ...ae, value: P } : ae
                );
                z(A), p(A);
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
              z(y), p(y);
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
function tp({
  values: e,
  options: t,
  onChange: n,
  disabled: r = !1,
  className: l,
  description: o,
  emptyState: i,
  unknownLabel: u = "unknown value"
}) {
  const s = bn(e), c = new Set(t.map((f) => f.value)), g = s.filter((f) => !c.has(f));
  function h(f) {
    n(bn(f));
  }
  return /* @__PURE__ */ m.jsxs("div", { className: ["structured-input-editor", l].filter(Boolean).join(" "), children: [
    o ? /* @__PURE__ */ m.jsx("div", { className: "structured-input-description", children: o }) : null,
    /* @__PURE__ */ m.jsx("div", { className: "structured-input-option-grid", children: t.map((f) => {
      const w = s.includes(f.value);
      return /* @__PURE__ */ m.jsxs("label", { className: "checkbox-row structured-input-option-row", children: [
        /* @__PURE__ */ m.jsx(
          "input",
          {
            type: "checkbox",
            checked: w,
            onChange: (k) => {
              const E = k.target.checked ? [...s, f.value] : s.filter((z) => z !== f.value);
              h(E);
            },
            disabled: r
          }
        ),
        /* @__PURE__ */ m.jsx("span", { children: f.label })
      ] }, f.value);
    }) }),
    s.length === 0 ? Bi(i, "No selections are configured.") : null,
    g.map((f) => /* @__PURE__ */ m.jsxs("div", { className: "structured-input-unknown-row", children: [
      /* @__PURE__ */ m.jsxs("div", { className: "structured-input-unknown-copy", children: [
        /* @__PURE__ */ m.jsx("strong", { children: f }),
        /* @__PURE__ */ m.jsx("span", { className: "status-pill", children: u })
      ] }),
      /* @__PURE__ */ m.jsx("div", { className: "inline-actions compact-actions", children: /* @__PURE__ */ m.jsx(
        "button",
        {
          type: "button",
          className: "danger",
          onClick: () => h(s.filter((w) => w !== f)),
          disabled: r,
          children: "Remove"
        }
      ) })
    ] }, f))
  ] });
}
function np({
  value: e,
  onChange: t,
  onValidationChange: n,
  fieldLabel: r,
  disabled: l = !1,
  className: o,
  description: i,
  emptyState: u,
  addLabel: s = "Add row",
  keyLabel: c = "Key",
  valueLabel: g = "Value"
}) {
  const h = JSON.stringify(e ?? {}), [f, w] = O.useState(() => es(e)), [k, E] = O.useState(null), z = O.useRef(h), d = O.useRef(null);
  O.useEffect(() => {
    xc(z, d, h) && (w(es(e)), E(null));
  }, [h, e]);
  function a(v) {
    try {
      const _ = ts(v, r, !1) ?? {};
      d.current = JSON.stringify(_), t(_), E(null);
    } catch (C) {
      E(C instanceof Error ? C.message : String(C));
    }
  }
  const p = k ?? (() => {
    try {
      return ts(f, r, !1), null;
    } catch (v) {
      return v instanceof Error ? v.message : String(v);
    }
  })();
  return O.useEffect(() => {
    n == null || n(p);
  }, [n, p]), /* @__PURE__ */ m.jsxs("div", { className: ["structured-input-editor", o].filter(Boolean).join(" "), children: [
    i ? /* @__PURE__ */ m.jsx("div", { className: "structured-input-description", children: i }) : null,
    /* @__PURE__ */ m.jsx("div", { className: "inline-actions compact-actions", children: /* @__PURE__ */ m.jsx(
      "button",
      {
        type: "button",
        onClick: () => w((v) => [...v, Sc()]),
        disabled: l,
        children: s
      }
    ) }),
    k ? /* @__PURE__ */ m.jsx("div", { className: "callout error-callout", children: k }) : null,
    f.length === 0 ? Bi(u, `No ${r.toLowerCase()} rows are configured.`) : null,
    f.map((v, C) => /* @__PURE__ */ m.jsxs("div", { className: "string-list-selector-row structured-input-row structured-input-map-row", children: [
      /* @__PURE__ */ m.jsxs("label", { className: "field-stack structured-input-field", children: [
        /* @__PURE__ */ m.jsx("span", { children: c }),
        /* @__PURE__ */ m.jsx(
          "input",
          {
            value: v.key,
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
        /* @__PURE__ */ m.jsx("span", { children: g }),
        /* @__PURE__ */ m.jsx(
          "input",
          {
            value: v.value,
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
    ] }, v.id))
  ] });
}
const rp = "";
class lp extends Error {
  constructor(n, r, l) {
    super(n);
    Cl(this, "status");
    Cl(this, "payload");
    this.name = "ApiError", this.status = r, this.payload = l;
  }
}
async function Hi(e, t) {
  const n = await fetch(`${rp}${e}`, {
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
    const o = await n.text();
    throw new Error(o.trim() || `${n.status} ${n.statusText}`);
  }
  if (!n.ok && typeof l == "object" && l && "error" in l)
    throw new lp(String(l.error), n.status, l);
  return l;
}
function ls() {
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
function bl(e) {
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
function Ec(e) {
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
function ip(e, t) {
  const n = {
    backend: e.backend,
    session_id: _e(e.sessionId),
    directory_path: _e(t)
  }, r = Ec(e.ttlDuration);
  r != null && (n.ttl_seconds = r);
  const l = _e(e.model);
  l && (n.model = l);
  const o = _e(e.agentProvider);
  o && (n.agent_provider = o);
  const i = _e(e.modelProviderMode);
  if (i && (n.model_provider_mode = i), e.backend === "libvirt_vm") {
    const h = _e(e.provider);
    n.vm_name = _e(e.vmName), h && (n.provider = h), n.vm_user = _e(e.vmUser) ?? "agent";
    const f = _e(e.vmImage);
    f && (n.vm_image = f), n.dotnet = e.dotnet, n.nuget = e.nuget, n.mssql = e.mssql, n.mssql_init_root = e.mssql ? _e(e.mssqlInitRoot) : null, n.playwright = e.playwright;
  } else
    n.target_name = _e(e.targetName), n.container_image = _e(e.containerImage);
  const u = e.initialPolicy;
  Object.keys(u.labels).length > 0 && (n.labels = u.labels), u.webSearchEnabled && (n.web_search_enabled = !0);
  const s = bl(u.allowedTools);
  s.length > 0 && (n.allowed_tools = s);
  const c = bl(u.allowedScriptNames);
  c.length > 0 && (n.allowed_script_names = c);
  const g = bl(u.communicationTargetIds);
  return g.length > 0 && (n.communication_target_ids = g), n;
}
function up(e) {
  return e.vmName.trim() || e.targetName.trim() || e.sessionId.trim() || "agent";
}
async function sp(e) {
  const t = new URLSearchParams(), n = e.trim();
  n && t.set("directory_path", n);
  const r = t.toString();
  return Hi(
    `/api/admin/agents/create-options${r ? `?${r}` : ""}`
  );
}
async function ap(e) {
  return (await Hi(
    "/api/admin/agents/create-operations",
    {
      method: "POST",
      body: JSON.stringify(e)
    }
  )).operation;
}
async function cp(e) {
  return (await Hi(
    `/api/admin/agents/create-operations/${encodeURIComponent(e)}`
  )).operation;
}
function fp(e) {
  return e.status === "queued" || e.status === "running";
}
function dp(e) {
  var r;
  const t = (r = e.session_id) == null ? void 0 : r.trim();
  if (t) return t;
  const n = e.result;
  if (!n) return null;
  for (const l of ["session_id", "sessionId"]) {
    const o = n[l];
    if (typeof o == "string" && o.trim()) return o.trim();
  }
  for (const l of ["session", "agent"]) {
    const o = n[l];
    if (typeof o != "object" || o === null || Array.isArray(o)) continue;
    const i = o.session_id;
    if (typeof i == "string" && i.trim()) return i.trim();
  }
  return null;
}
function H({ children: e, tooltip: t }) {
  const n = typeof e == "string" ? e : "Field";
  return /* @__PURE__ */ m.jsxs("span", { className: "config-field-label", children: [
    /* @__PURE__ */ m.jsx("span", { children: e }),
    /* @__PURE__ */ m.jsx(
      qd,
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
function pp(e, t, n) {
  const r = ((n == null ? void 0 : n.agent_providers) ?? []).some(
    (o) => o.value === e.agentProvider && o.execution_backends.includes(t)
  ), l = e.agentProvider && r ? e.agentProvider : "";
  return {
    ...e,
    backend: t,
    agentProvider: l,
    modelProviderMode: l && l !== "codex" ? "" : e.modelProviderMode
  };
}
function mp(e) {
  const t = e.trim();
  return !t || Ec(t) == null;
}
function hp({
  directoryPath: e,
  onClose: t,
  onCreate: n,
  title: r = "Create agent",
  description: l = null,
  submitLabel: o = "Create",
  submittingLabel: i = "Queueing...",
  closeLabel: u = "Close",
  privilegedUnlocked: s = !0,
  closeOnSuccess: c = !0,
  requireSessionId: g = !1,
  requireSessionIdMessage: h = "Session id is required."
}) {
  const [f, w] = O.useState(() => ls()), [k, E] = O.useState(null), [z, d] = O.useState(""), [a, p] = O.useState(null), [v, C] = O.useState(!1);
  O.useEffect(() => {
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
        const A = await sp(e);
        y || E(A);
      } catch {
        y || E(null);
      }
    })(), () => {
      y = !0;
    };
  }, [e]);
  const _ = O.useMemo(
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
    if (g && !f.sessionId.trim()) {
      d(h);
      return;
    }
    if (f.backend === "container_worker_vm" && !f.sessionId.trim()) {
      d("Session id is required for container workers.");
      return;
    }
    if (mp(f.ttlDuration)) {
      d(
        "Session TTL must use seconds, minutes, hours, days, or weeks, for example 30m, 8h, 7d, or 2w."
      );
      return;
    }
    if (a) {
      d(a);
      return;
    }
    const y = f, P = ip(y, e), A = up(y);
    C(!0), d("");
    try {
      await n(P, { form: y, targetLabel: A }), w(ls()), p(null), c && t();
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
      /* @__PURE__ */ m.jsx("button", { className: "admin-action-button compact", type: "button", onClick: t, disabled: v, children: u })
    ] }),
    /* @__PURE__ */ m.jsxs("div", { className: "modal-body", children: [
      z ? /* @__PURE__ */ m.jsx("div", { className: "callout error-callout", children: z }) : null,
      /* @__PURE__ */ m.jsxs("div", { className: "config-field-grid", children: [
        /* @__PURE__ */ m.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ m.jsx(H, { tooltip: "Execution target for the agent. VM agent launches a libvirt VM; Container worker prepares a worker session that runs in a managed container.", children: "Backend" }),
          /* @__PURE__ */ m.jsx("span", { className: "select-field-control", children: /* @__PURE__ */ m.jsxs(
            "select",
            {
              value: f.backend,
              onChange: (y) => {
                const P = y.target.value;
                w((A) => pp(A, P, k));
              },
              children: [
                /* @__PURE__ */ m.jsx("option", { value: "libvirt_vm", children: "VM agent" }),
                /* @__PURE__ */ m.jsx("option", { value: "container_worker_vm", children: "Container worker" })
              ]
            }
          ) })
        ] }),
        /* @__PURE__ */ m.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ m.jsx(H, { tooltip: "Agent CLI to bootstrap and run in the target. Claude bootstraps Claude Code for VM agents; Directory default uses the configured provider default.", children: "Agent provider" }),
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
          /* @__PURE__ */ m.jsx(H, { tooltip: "Relay mode for Codex model requests. Leave blank to use the directory default; choose Local OpenAI for host-local Ollama.", children: "Model relay" }),
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
          /* @__PURE__ */ m.jsx(H, { tooltip: "Host-side session id for policy, logs, model routing, and resume state. Required for container workers; VM agents can derive one from the VM name.", children: "Session" }),
          /* @__PURE__ */ m.jsx(
            "input",
            {
              value: f.sessionId,
              onChange: (y) => w((P) => ({ ...P, sessionId: y.target.value })),
              placeholder: g || f.backend === "container_worker_vm" ? "Required" : "Optional"
            }
          )
        ] }),
        /* @__PURE__ */ m.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ m.jsx(H, { tooltip: "How long the created session remains active before expiry. Use s, m, h, d, or w, for example 30m, 8h, 7d, or 2w.", children: "Session TTL" }),
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
          /* @__PURE__ */ m.jsx(H, { tooltip: "Model stored on the session for runtime prompts and resumes. Leave blank to use the selected agent provider or directory default.", children: "Model" }),
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
            /* @__PURE__ */ m.jsx(H, { tooltip: "Libvirt domain name to create for this VM-backed agent. It must be unique on the host.", children: "VM name" }),
            /* @__PURE__ */ m.jsx(
              "input",
              {
                value: f.vmName,
                onChange: (y) => w((P) => ({ ...P, vmName: y.target.value }))
              }
            )
          ] }),
          /* @__PURE__ */ m.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ m.jsx(H, { tooltip: "VM infrastructure provider, not the model provider. Leave blank to use the directory default, usually libvirt.", children: "Provider" }),
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
            /* @__PURE__ */ m.jsx(H, { tooltip: "Guest runtime user used for bootstraps and agent commands. Current standard VM images use agent.", children: "VM user" }),
            /* @__PURE__ */ m.jsx(
              "input",
              {
                value: f.vmUser,
                onChange: (y) => w((P) => ({ ...P, vmUser: y.target.value }))
              }
            )
          ] }),
          /* @__PURE__ */ m.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ m.jsx(H, { tooltip: "Approved libvirt template alias for the base qcow2 image. Leave blank to use the directory default.", children: "VM image" }),
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
            /* @__PURE__ */ m.jsx(H, { tooltip: "Display and control-plane target name for this container-worker agent. Leave blank to reuse the session id.", children: "Target name" }),
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
            /* @__PURE__ */ m.jsx(H, { tooltip: "Approved container image alias for worker sessions. Leave blank to use the directory default runner image.", children: "Container image selector" }),
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
        f.backend === "libvirt_vm" ? /* @__PURE__ */ m.jsxs(m.Fragment, { children: [
          /* @__PURE__ */ m.jsxs("label", { className: "checkbox-row agent-checkbox-row", children: [
            /* @__PURE__ */ m.jsx(
              "input",
              {
                type: "checkbox",
                checked: f.dotnet,
                onChange: (y) => w((P) => ({ ...P, dotnet: y.target.checked }))
              }
            ),
            /* @__PURE__ */ m.jsx(H, { tooltip: "Copy the host .NET SDK into the VM after launch.", children: ".NET" })
          ] }),
          /* @__PURE__ */ m.jsxs("label", { className: "checkbox-row agent-checkbox-row", children: [
            /* @__PURE__ */ m.jsx(
              "input",
              {
                type: "checkbox",
                checked: f.nuget,
                onChange: (y) => w((P) => ({ ...P, nuget: y.target.checked }))
              }
            ),
            /* @__PURE__ */ m.jsx(H, { tooltip: "Configure the guest to restore packages through the host NuGet relay and cache.", children: "NuGet" })
          ] }),
          /* @__PURE__ */ m.jsxs("label", { className: "checkbox-row agent-checkbox-row", children: [
            /* @__PURE__ */ m.jsx(
              "input",
              {
                type: "checkbox",
                checked: f.mssql,
                onChange: (y) => w((P) => ({ ...P, mssql: y.target.checked }))
              }
            ),
            /* @__PURE__ */ m.jsx(H, { tooltip: "Provision a guest-local SQL Server instance. Requires MSSQL_SA_PASSWORD on the host.", children: "MSSQL" })
          ] }),
          /* @__PURE__ */ m.jsxs("label", { className: "checkbox-row agent-checkbox-row", children: [
            /* @__PURE__ */ m.jsx(
              "input",
              {
                type: "checkbox",
                checked: f.playwright,
                onChange: (y) => w((P) => ({ ...P, playwright: y.target.checked }))
              }
            ),
            /* @__PURE__ */ m.jsx(H, { tooltip: "Install host-seeded Node and Playwright browser dependencies in the VM.", children: "Playwright" })
          ] }),
          /* @__PURE__ */ m.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ m.jsx(H, { tooltip: "Optional host directory copied into the SQL init folder. If init.sql exists there, it runs after SQL Server starts.", children: "MSSQL init root" }),
            /* @__PURE__ */ m.jsx(
              "input",
              {
                value: f.mssqlInitRoot,
                onChange: (y) => w((P) => ({ ...P, mssqlInitRoot: y.target.value })),
                disabled: !f.mssql
              }
            )
          ] })
        ] }) : null,
        /* @__PURE__ */ m.jsxs("div", { className: "field-stack wide-field", children: [
          /* @__PURE__ */ m.jsx(H, { tooltip: "Session policy values written when the agent is created. Empty rows are ignored.", children: "Initial policy" }),
          /* @__PURE__ */ m.jsx("p", { className: "field-description", children: "Optional session policy values to apply when the agent is created. Blank rows are not sent." })
        ] }),
        /* @__PURE__ */ m.jsxs("div", { className: "field-stack", children: [
          /* @__PURE__ */ m.jsx(H, { tooltip: "Key/value metadata stored on the session and shown in agent lists.", children: "Labels" }),
          /* @__PURE__ */ m.jsx(
            np,
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
          /* @__PURE__ */ m.jsx(H, { tooltip: "MCP tools this session may call without being hidden by policy. Approval rules still apply.", children: "Allowed tools" }),
          /* @__PURE__ */ m.jsx(
            rs,
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
        /* @__PURE__ */ m.jsxs("label", { className: "checkbox-row agent-checkbox-row", children: [
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
          /* @__PURE__ */ m.jsx(H, { tooltip: "Allow this session's agent runner to use live web search when the selected provider supports it.", children: "Web search" })
        ] }),
        /* @__PURE__ */ m.jsxs("div", { className: "field-stack", children: [
          /* @__PURE__ */ m.jsx(H, { tooltip: "Script names this session may run through approved script execution paths.", children: "Allowed scripts" }),
          /* @__PURE__ */ m.jsx(
            rs,
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
        /* @__PURE__ */ m.jsxs("div", { className: "field-stack", children: [
          /* @__PURE__ */ m.jsx(H, { tooltip: "Directory communication targets the session may use for messaging. Only registered targets with session-compatible capabilities are selectable.", children: "Communication targets" }),
          /* @__PURE__ */ m.jsx(
            tp,
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
      /* @__PURE__ */ m.jsx("div", { className: "inline-actions compact-actions", children: /* @__PURE__ */ m.jsx("button", { className: "admin-action-button primary compact", type: "button", disabled: !s || v, onClick: () => void I(), children: v ? i : o }) })
    ] })
  ] }) });
}
const vp = 1500, gp = 10 * 60 * 1e3, os = "Session id is required so the created core session can be registered in Team.";
function yp(e) {
  return new Promise((t) => window.setTimeout(t, e));
}
async function wp(e) {
  let t = e;
  const n = Date.now() + gp;
  for (; fp(t); ) {
    if (Date.now() >= n)
      throw new Error(`Timed out waiting for ${t.target_name} to finish creating.`);
    await yp(vp), t = await cp(t.operation_id);
  }
  if (t.status === "failed")
    throw new Error(t.error || `Agent create failed for ${t.target_name}.`);
  return t;
}
function kp(e) {
  window.dispatchEvent(new CustomEvent("team-agent-create:created", { detail: e }));
}
function Sp() {
  return new URLSearchParams(window.location.search).get("privileged_unlocked") === "true";
}
function xp() {
  var i;
  const [e, t] = O.useState(null), [n, r] = O.useState(""), l = Sp();
  if (O.useEffect(() => {
    const u = (c) => {
      r(""), t({
        contextId: (c == null ? void 0 : c.contextId) ?? null,
        directoryPath: (c == null ? void 0 : c.directoryPath) ?? "root"
      });
    };
    window.openTeamAgentCreateDialog = u;
    const s = (c) => {
      u(c.detail ?? {});
    };
    return window.addEventListener("team-agent-create:open", s), () => {
      window.openTeamAgentCreateDialog === u && delete window.openTeamAgentCreateDialog, window.removeEventListener("team-agent-create:open", s);
    };
  }, []), !e)
    return null;
  async function o(u) {
    var h;
    if (!l)
      throw new Error("Unlock privileged operations to create a core agent session.");
    if (!((h = u.session_id) != null && h.trim()))
      throw new Error(os);
    r("Queueing core agent session.");
    const s = await ap(u);
    r(`Creating ${s.target_name}.`);
    const c = await wp(s), g = dp(c);
    if (!g)
      throw new Error(`Created ${c.target_name}, but the create operation did not return a session id.`);
    r("Registering Team agent."), kp({
      contextId: (e == null ? void 0 : e.contextId) ?? null,
      sessionId: g,
      targetName: c.target_name,
      operation: c
    });
  }
  return /* @__PURE__ */ m.jsx(
    hp,
    {
      directoryPath: ((i = e.directoryPath) == null ? void 0 : i.trim()) || "root",
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
function Ep() {
  let e = document.querySelector("#team-agent-create-root");
  return e || (e = document.createElement("div"), e.id = "team-agent-create-root", document.body.appendChild(e)), e;
}
let eo = null;
function is() {
  const e = Ep();
  eo || (eo = kc(e)), eo.render(/* @__PURE__ */ m.jsx(xp, {}));
}
document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", is, { once: !0 }) : is();
