var Ec = Object.defineProperty;
var Cc = (e, t, n) => t in e ? Ec(e, t, { enumerable: !0, configurable: !0, writable: !0, value: n }) : e[t] = n;
var Cl = (e, t, n) => Cc(e, typeof t != "symbol" ? t + "" : t, n);
var is = { exports: {} }, ul = {}, us = { exports: {} }, M = {};
/**
 * @license React
 * react.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var er = Symbol.for("react.element"), _c = Symbol.for("react.portal"), Nc = Symbol.for("react.fragment"), Pc = Symbol.for("react.strict_mode"), jc = Symbol.for("react.profiler"), Lc = Symbol.for("react.provider"), Tc = Symbol.for("react.context"), zc = Symbol.for("react.forward_ref"), Rc = Symbol.for("react.suspense"), Mc = Symbol.for("react.memo"), Ic = Symbol.for("react.lazy"), Qi = Symbol.iterator;
function Dc(e) {
  return e === null || typeof e != "object" ? null : (e = Qi && e[Qi] || e["@@iterator"], typeof e == "function" ? e : null);
}
var ss = { isMounted: function() {
  return !1;
}, enqueueForceUpdate: function() {
}, enqueueReplaceState: function() {
}, enqueueSetState: function() {
} }, as = Object.assign, cs = {};
function an(e, t, n) {
  this.props = e, this.context = t, this.refs = cs, this.updater = n || ss;
}
an.prototype.isReactComponent = {};
an.prototype.setState = function(e, t) {
  if (typeof e != "object" && typeof e != "function" && e != null) throw Error("setState(...): takes an object of state variables to update or a function which returns an object of state variables.");
  this.updater.enqueueSetState(this, e, t, "setState");
};
an.prototype.forceUpdate = function(e) {
  this.updater.enqueueForceUpdate(this, e, "forceUpdate");
};
function fs() {
}
fs.prototype = an.prototype;
function Xo(e, t, n) {
  this.props = e, this.context = t, this.refs = cs, this.updater = n || ss;
}
var Go = Xo.prototype = new fs();
Go.constructor = Xo;
as(Go, an.prototype);
Go.isPureReactComponent = !0;
var Wi = Array.isArray, ds = Object.prototype.hasOwnProperty, Zo = { current: null }, ps = { key: !0, ref: !0, __self: !0, __source: !0 };
function ms(e, t, n) {
  var r, l = {}, o = null, i = null;
  if (t != null) for (r in t.ref !== void 0 && (i = t.ref), t.key !== void 0 && (o = "" + t.key), t) ds.call(t, r) && !ps.hasOwnProperty(r) && (l[r] = t[r]);
  var u = arguments.length - 2;
  if (u === 1) l.children = n;
  else if (1 < u) {
    for (var s = Array(u), c = 0; c < u; c++) s[c] = arguments[c + 2];
    l.children = s;
  }
  if (e && e.defaultProps) for (r in u = e.defaultProps, u) l[r] === void 0 && (l[r] = u[r]);
  return { $$typeof: er, type: e, key: o, ref: i, props: l, _owner: Zo.current };
}
function Oc(e, t) {
  return { $$typeof: er, type: e.type, key: t, ref: e.ref, props: e.props, _owner: e._owner };
}
function Jo(e) {
  return typeof e == "object" && e !== null && e.$$typeof === er;
}
function Fc(e) {
  var t = { "=": "=0", ":": "=2" };
  return "$" + e.replace(/[=:]/g, function(n) {
    return t[n];
  });
}
var Ki = /\/+/g;
function _l(e, t) {
  return typeof e == "object" && e !== null && e.key != null ? Fc("" + e.key) : t.toString(36);
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
        case _c:
          i = !0;
      }
  }
  if (i) return i = e, l = l(i), e = r === "" ? "." + _l(i, 0) : r, Wi(l) ? (n = "", e != null && (n = e.replace(Ki, "$&/") + "/"), _r(l, t, n, "", function(c) {
    return c;
  })) : l != null && (Jo(l) && (l = Oc(l, n + (!l.key || i && i.key === l.key ? "" : ("" + l.key).replace(Ki, "$&/") + "/") + e)), t.push(l)), 1;
  if (i = 0, r = r === "" ? "." : r + ":", Wi(e)) for (var u = 0; u < e.length; u++) {
    o = e[u];
    var s = r + _l(o, u);
    i += _r(o, t, n, s, l);
  }
  else if (s = Dc(e), typeof s == "function") for (e = s.call(e), u = 0; !(o = e.next()).done; ) o = o.value, s = r + _l(o, u++), i += _r(o, t, n, s, l);
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
function Uc(e) {
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
var ce = { current: null }, Nr = { transition: null }, Ac = { ReactCurrentDispatcher: ce, ReactCurrentBatchConfig: Nr, ReactCurrentOwner: Zo };
function hs() {
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
M.Fragment = Nc;
M.Profiler = jc;
M.PureComponent = Xo;
M.StrictMode = Pc;
M.Suspense = Rc;
M.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED = Ac;
M.act = hs;
M.cloneElement = function(e, t, n) {
  if (e == null) throw Error("React.cloneElement(...): The argument must be a React element, but you passed " + e + ".");
  var r = as({}, e.props), l = e.key, o = e.ref, i = e._owner;
  if (t != null) {
    if (t.ref !== void 0 && (o = t.ref, i = Zo.current), t.key !== void 0 && (l = "" + t.key), e.type && e.type.defaultProps) var u = e.type.defaultProps;
    for (s in t) ds.call(t, s) && !ps.hasOwnProperty(s) && (r[s] = t[s] === void 0 && u !== void 0 ? u[s] : t[s]);
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
  return e = { $$typeof: Tc, _currentValue: e, _currentValue2: e, _threadCount: 0, Provider: null, Consumer: null, _defaultValue: null, _globalName: null }, e.Provider = { $$typeof: Lc, _context: e }, e.Consumer = e;
};
M.createElement = ms;
M.createFactory = function(e) {
  var t = ms.bind(null, e);
  return t.type = e, t;
};
M.createRef = function() {
  return { current: null };
};
M.forwardRef = function(e) {
  return { $$typeof: zc, render: e };
};
M.isValidElement = Jo;
M.lazy = function(e) {
  return { $$typeof: Ic, _payload: { _status: -1, _result: e }, _init: Uc };
};
M.memo = function(e, t) {
  return { $$typeof: Mc, type: e, compare: t === void 0 ? null : t };
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
M.unstable_act = hs;
M.useCallback = function(e, t) {
  return ce.current.useCallback(e, t);
};
M.useContext = function(e) {
  return ce.current.useContext(e);
};
M.useDebugValue = function() {
};
M.useDeferredValue = function(e) {
  return ce.current.useDeferredValue(e);
};
M.useEffect = function(e, t) {
  return ce.current.useEffect(e, t);
};
M.useId = function() {
  return ce.current.useId();
};
M.useImperativeHandle = function(e, t, n) {
  return ce.current.useImperativeHandle(e, t, n);
};
M.useInsertionEffect = function(e, t) {
  return ce.current.useInsertionEffect(e, t);
};
M.useLayoutEffect = function(e, t) {
  return ce.current.useLayoutEffect(e, t);
};
M.useMemo = function(e, t) {
  return ce.current.useMemo(e, t);
};
M.useReducer = function(e, t, n) {
  return ce.current.useReducer(e, t, n);
};
M.useRef = function(e) {
  return ce.current.useRef(e);
};
M.useState = function(e) {
  return ce.current.useState(e);
};
M.useSyncExternalStore = function(e, t, n) {
  return ce.current.useSyncExternalStore(e, t, n);
};
M.useTransition = function() {
  return ce.current.useTransition();
};
M.version = "18.3.1";
us.exports = M;
var D = us.exports;
/**
 * @license React
 * react-jsx-runtime.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var Vc = D, $c = Symbol.for("react.element"), Bc = Symbol.for("react.fragment"), Hc = Object.prototype.hasOwnProperty, Qc = Vc.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED.ReactCurrentOwner, Wc = { key: !0, ref: !0, __self: !0, __source: !0 };
function vs(e, t, n) {
  var r, l = {}, o = null, i = null;
  n !== void 0 && (o = "" + n), t.key !== void 0 && (o = "" + t.key), t.ref !== void 0 && (i = t.ref);
  for (r in t) Hc.call(t, r) && !Wc.hasOwnProperty(r) && (l[r] = t[r]);
  if (e && e.defaultProps) for (r in t = e.defaultProps, t) l[r] === void 0 && (l[r] = t[r]);
  return { $$typeof: $c, type: e, key: o, ref: i, props: l, _owner: Qc.current };
}
ul.Fragment = Bc;
ul.jsx = vs;
ul.jsxs = vs;
is.exports = ul;
var v = is.exports, gs = { exports: {} }, Se = {}, ys = { exports: {} }, ws = {};
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
  function t(P, T) {
    var R = P.length;
    P.push(T);
    e: for (; 0 < R; ) {
      var Y = R - 1 >>> 1, q = P[Y];
      if (0 < l(q, T)) P[Y] = T, P[R] = q, R = Y;
      else break e;
    }
  }
  function n(P) {
    return P.length === 0 ? null : P[0];
  }
  function r(P) {
    if (P.length === 0) return null;
    var T = P[0], R = P.pop();
    if (R !== T) {
      P[0] = R;
      e: for (var Y = 0, q = P.length, ir = q >>> 1; Y < ir; ) {
        var kt = 2 * (Y + 1) - 1, El = P[kt], St = kt + 1, ur = P[St];
        if (0 > l(El, R)) St < q && 0 > l(ur, El) ? (P[Y] = ur, P[St] = R, Y = St) : (P[Y] = El, P[kt] = R, Y = kt);
        else if (St < q && 0 > l(ur, R)) P[Y] = ur, P[St] = R, Y = St;
        else break e;
      }
    }
    return T;
  }
  function l(P, T) {
    var R = P.sortIndex - T.sortIndex;
    return R !== 0 ? R : P.id - T.id;
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
  var s = [], c = [], m = 1, h = null, d = 3, w = !1, C = !1, S = !1, z = typeof setTimeout == "function" ? setTimeout : null, f = typeof clearTimeout == "function" ? clearTimeout : null, a = typeof setImmediate < "u" ? setImmediate : null;
  typeof navigator < "u" && navigator.scheduling !== void 0 && navigator.scheduling.isInputPending !== void 0 && navigator.scheduling.isInputPending.bind(navigator.scheduling);
  function p(P) {
    for (var T = n(c); T !== null; ) {
      if (T.callback === null) r(c);
      else if (T.startTime <= P) r(c), T.sortIndex = T.expirationTime, t(s, T);
      else break;
      T = n(c);
    }
  }
  function y(P) {
    if (S = !1, p(P), !C) if (n(s) !== null) C = !0, pn(_);
    else {
      var T = n(c);
      T !== null && mn(y, T.startTime - P);
    }
  }
  function _(P, T) {
    C = !1, S && (S = !1, f(g), g = -1), w = !0;
    var R = d;
    try {
      for (p(T), h = n(s); h !== null && (!(h.expirationTime > T) || P && !W()); ) {
        var Y = h.callback;
        if (typeof Y == "function") {
          h.callback = null, d = h.priorityLevel;
          var q = Y(h.expirationTime <= T);
          T = e.unstable_now(), typeof q == "function" ? h.callback = q : h === n(s) && r(s), p(T);
        } else r(s);
        h = n(s);
      }
      if (h !== null) var ir = !0;
      else {
        var kt = n(c);
        kt !== null && mn(y, kt.startTime - T), ir = !1;
      }
      return ir;
    } finally {
      h = null, d = R, w = !1;
    }
  }
  var N = !1, x = null, g = -1, E = 5, L = -1;
  function W() {
    return !(e.unstable_now() - L < E);
  }
  function Ee() {
    if (x !== null) {
      var P = e.unstable_now();
      L = P;
      var T = !0;
      try {
        T = x(!0, P);
      } finally {
        T ? ze() : (N = !1, x = null);
      }
    } else N = !1;
  }
  var ze;
  if (typeof a == "function") ze = function() {
    a(Ee);
  };
  else if (typeof MessageChannel < "u") {
    var dn = new MessageChannel(), or = dn.port2;
    dn.port1.onmessage = Ee, ze = function() {
      or.postMessage(null);
    };
  } else ze = function() {
    z(Ee, 0);
  };
  function pn(P) {
    x = P, N || (N = !0, ze());
  }
  function mn(P, T) {
    g = z(function() {
      P(e.unstable_now());
    }, T);
  }
  e.unstable_IdlePriority = 5, e.unstable_ImmediatePriority = 1, e.unstable_LowPriority = 4, e.unstable_NormalPriority = 3, e.unstable_Profiling = null, e.unstable_UserBlockingPriority = 2, e.unstable_cancelCallback = function(P) {
    P.callback = null;
  }, e.unstable_continueExecution = function() {
    C || w || (C = !0, pn(_));
  }, e.unstable_forceFrameRate = function(P) {
    0 > P || 125 < P ? console.error("forceFrameRate takes a positive int between 0 and 125, forcing frame rates higher than 125 fps is not supported") : E = 0 < P ? Math.floor(1e3 / P) : 5;
  }, e.unstable_getCurrentPriorityLevel = function() {
    return d;
  }, e.unstable_getFirstCallbackNode = function() {
    return n(s);
  }, e.unstable_next = function(P) {
    switch (d) {
      case 1:
      case 2:
      case 3:
        var T = 3;
        break;
      default:
        T = d;
    }
    var R = d;
    d = T;
    try {
      return P();
    } finally {
      d = R;
    }
  }, e.unstable_pauseExecution = function() {
  }, e.unstable_requestPaint = function() {
  }, e.unstable_runWithPriority = function(P, T) {
    switch (P) {
      case 1:
      case 2:
      case 3:
      case 4:
      case 5:
        break;
      default:
        P = 3;
    }
    var R = d;
    d = P;
    try {
      return T();
    } finally {
      d = R;
    }
  }, e.unstable_scheduleCallback = function(P, T, R) {
    var Y = e.unstable_now();
    switch (typeof R == "object" && R !== null ? (R = R.delay, R = typeof R == "number" && 0 < R ? Y + R : Y) : R = Y, P) {
      case 1:
        var q = -1;
        break;
      case 2:
        q = 250;
        break;
      case 5:
        q = 1073741823;
        break;
      case 4:
        q = 1e4;
        break;
      default:
        q = 5e3;
    }
    return q = R + q, P = { id: m++, callback: T, priorityLevel: P, startTime: R, expirationTime: q, sortIndex: -1 }, R > Y ? (P.sortIndex = R, t(c, P), n(s) === null && P === n(c) && (S ? (f(g), g = -1) : S = !0, mn(y, R - Y))) : (P.sortIndex = q, t(s, P), C || w || (C = !0, pn(_))), P;
  }, e.unstable_shouldYield = W, e.unstable_wrapCallback = function(P) {
    var T = d;
    return function() {
      var R = d;
      d = T;
      try {
        return P.apply(this, arguments);
      } finally {
        d = R;
      }
    };
  };
})(ws);
ys.exports = ws;
var Kc = ys.exports;
/**
 * @license React
 * react-dom.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var Yc = D, ke = Kc;
function k(e) {
  for (var t = "https://reactjs.org/docs/error-decoder.html?invariant=" + e, n = 1; n < arguments.length; n++) t += "&args[]=" + encodeURIComponent(arguments[n]);
  return "Minified React error #" + e + "; visit " + t + " for the full message or use the non-minified dev environment for full errors and additional helpful warnings.";
}
var ks = /* @__PURE__ */ new Set(), On = {};
function It(e, t) {
  tn(e, t), tn(e + "Capture", t);
}
function tn(e, t) {
  for (On[e] = t, e = 0; e < t.length; e++) ks.add(t[e]);
}
var Ge = !(typeof window > "u" || typeof window.document > "u" || typeof window.document.createElement > "u"), to = Object.prototype.hasOwnProperty, Xc = /^[:A-Z_a-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF\u0370-\u037D\u037F-\u1FFF\u200C-\u200D\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD][:A-Z_a-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF\u0370-\u037D\u037F-\u1FFF\u200C-\u200D\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD\-.0-9\u00B7\u0300-\u036F\u203F-\u2040]*$/, Yi = {}, Xi = {};
function Gc(e) {
  return to.call(Xi, e) ? !0 : to.call(Yi, e) ? !1 : Xc.test(e) ? Xi[e] = !0 : (Yi[e] = !0, !1);
}
function Zc(e, t, n, r) {
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
function Jc(e, t, n, r) {
  if (t === null || typeof t > "u" || Zc(e, t, n, r)) return !0;
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
function fe(e, t, n, r, l, o, i) {
  this.acceptsBooleans = t === 2 || t === 3 || t === 4, this.attributeName = r, this.attributeNamespace = l, this.mustUseProperty = n, this.propertyName = e, this.type = t, this.sanitizeURL = o, this.removeEmptyString = i;
}
var re = {};
"children dangerouslySetInnerHTML defaultValue defaultChecked innerHTML suppressContentEditableWarning suppressHydrationWarning style".split(" ").forEach(function(e) {
  re[e] = new fe(e, 0, !1, e, null, !1, !1);
});
[["acceptCharset", "accept-charset"], ["className", "class"], ["htmlFor", "for"], ["httpEquiv", "http-equiv"]].forEach(function(e) {
  var t = e[0];
  re[t] = new fe(t, 1, !1, e[1], null, !1, !1);
});
["contentEditable", "draggable", "spellCheck", "value"].forEach(function(e) {
  re[e] = new fe(e, 2, !1, e.toLowerCase(), null, !1, !1);
});
["autoReverse", "externalResourcesRequired", "focusable", "preserveAlpha"].forEach(function(e) {
  re[e] = new fe(e, 2, !1, e, null, !1, !1);
});
"allowFullScreen async autoFocus autoPlay controls default defer disabled disablePictureInPicture disableRemotePlayback formNoValidate hidden loop noModule noValidate open playsInline readOnly required reversed scoped seamless itemScope".split(" ").forEach(function(e) {
  re[e] = new fe(e, 3, !1, e.toLowerCase(), null, !1, !1);
});
["checked", "multiple", "muted", "selected"].forEach(function(e) {
  re[e] = new fe(e, 3, !0, e, null, !1, !1);
});
["capture", "download"].forEach(function(e) {
  re[e] = new fe(e, 4, !1, e, null, !1, !1);
});
["cols", "rows", "size", "span"].forEach(function(e) {
  re[e] = new fe(e, 6, !1, e, null, !1, !1);
});
["rowSpan", "start"].forEach(function(e) {
  re[e] = new fe(e, 5, !1, e.toLowerCase(), null, !1, !1);
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
  re[t] = new fe(t, 1, !1, e, null, !1, !1);
});
"xlink:actuate xlink:arcrole xlink:role xlink:show xlink:title xlink:type".split(" ").forEach(function(e) {
  var t = e.replace(qo, bo);
  re[t] = new fe(t, 1, !1, e, "http://www.w3.org/1999/xlink", !1, !1);
});
["xml:base", "xml:lang", "xml:space"].forEach(function(e) {
  var t = e.replace(qo, bo);
  re[t] = new fe(t, 1, !1, e, "http://www.w3.org/XML/1998/namespace", !1, !1);
});
["tabIndex", "crossOrigin"].forEach(function(e) {
  re[e] = new fe(e, 1, !1, e.toLowerCase(), null, !1, !1);
});
re.xlinkHref = new fe("xlinkHref", 1, !1, "xlink:href", "http://www.w3.org/1999/xlink", !0, !1);
["src", "href", "action", "formAction"].forEach(function(e) {
  re[e] = new fe(e, 1, !1, e.toLowerCase(), null, !0, !0);
});
function ei(e, t, n, r) {
  var l = re.hasOwnProperty(t) ? re[t] : null;
  (l !== null ? l.type !== 0 : r || !(2 < t.length) || t[0] !== "o" && t[0] !== "O" || t[1] !== "n" && t[1] !== "N") && (Jc(t, n, l, r) && (n = null), r || l === null ? Gc(t) && (n === null ? e.removeAttribute(t) : e.setAttribute(t, "" + n)) : l.mustUseProperty ? e[l.propertyName] = n === null ? l.type === 3 ? !1 : "" : n : (t = l.attributeName, r = l.attributeNamespace, n === null ? e.removeAttribute(t) : (l = l.type, n = l === 3 || l === 4 && n === !0 ? "" : "" + n, r ? e.setAttributeNS(r, t, n) : e.setAttribute(t, n))));
}
var be = Yc.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED, ar = Symbol.for("react.element"), Ft = Symbol.for("react.portal"), Ut = Symbol.for("react.fragment"), ti = Symbol.for("react.strict_mode"), no = Symbol.for("react.profiler"), Ss = Symbol.for("react.provider"), xs = Symbol.for("react.context"), ni = Symbol.for("react.forward_ref"), ro = Symbol.for("react.suspense"), lo = Symbol.for("react.suspense_list"), ri = Symbol.for("react.memo"), tt = Symbol.for("react.lazy"), Es = Symbol.for("react.offscreen"), Gi = Symbol.iterator;
function hn(e) {
  return e === null || typeof e != "object" ? null : (e = Gi && e[Gi] || e["@@iterator"], typeof e == "function" ? e : null);
}
var Q = Object.assign, Nl;
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
function qc(e) {
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
    case xs:
      return (e.displayName || "Context") + ".Consumer";
    case Ss:
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
function bc(e) {
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
function Cs(e) {
  var t = e.type;
  return (e = e.nodeName) && e.toLowerCase() === "input" && (t === "checkbox" || t === "radio");
}
function ef(e) {
  var t = Cs(e) ? "checked" : "value", n = Object.getOwnPropertyDescriptor(e.constructor.prototype, t), r = "" + e[t];
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
  e._valueTracker || (e._valueTracker = ef(e));
}
function _s(e) {
  if (!e) return !1;
  var t = e._valueTracker;
  if (!t) return !0;
  var n = t.getValue(), r = "";
  return e && (r = Cs(e) ? e.checked ? "true" : "false" : e.value), e = r, e !== n ? (t.setValue(e), !0) : !1;
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
  return Q({}, t, { defaultChecked: void 0, defaultValue: void 0, value: void 0, checked: n ?? e._wrapperState.initialChecked });
}
function Zi(e, t) {
  var n = t.defaultValue == null ? "" : t.defaultValue, r = t.checked != null ? t.checked : t.defaultChecked;
  n = ht(t.value != null ? t.value : n), e._wrapperState = { initialChecked: r, initialValue: n, controlled: t.type === "checkbox" || t.type === "radio" ? t.checked != null : t.value != null };
}
function Ns(e, t) {
  t = t.checked, t != null && ei(e, "checked", t, !1);
}
function uo(e, t) {
  Ns(e, t);
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
  if (t.dangerouslySetInnerHTML != null) throw Error(k(91));
  return Q({}, t, { value: void 0, defaultValue: void 0, children: "" + e._wrapperState.initialValue });
}
function qi(e, t) {
  var n = t.value;
  if (n == null) {
    if (n = t.children, t = t.defaultValue, n != null) {
      if (t != null) throw Error(k(92));
      if (Cn(n)) {
        if (1 < n.length) throw Error(k(93));
        n = n[0];
      }
      t = n;
    }
    t == null && (t = ""), n = t;
  }
  e._wrapperState = { initialValue: ht(n) };
}
function Ps(e, t) {
  var n = ht(t.value), r = ht(t.defaultValue);
  n != null && (n = "" + n, n !== e.value && (e.value = n), t.defaultValue == null && e.defaultValue !== n && (e.defaultValue = n)), r != null && (e.defaultValue = "" + r);
}
function bi(e) {
  var t = e.textContent;
  t === e._wrapperState.initialValue && t !== "" && t !== null && (e.value = t);
}
function js(e) {
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
  return e == null || e === "http://www.w3.org/1999/xhtml" ? js(t) : e === "http://www.w3.org/2000/svg" && t === "foreignObject" ? "http://www.w3.org/1999/xhtml" : e;
}
var fr, Ls = function(e) {
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
}, tf = ["Webkit", "ms", "Moz", "O"];
Object.keys(Pn).forEach(function(e) {
  tf.forEach(function(t) {
    t = t + e.charAt(0).toUpperCase() + e.substring(1), Pn[t] = Pn[e];
  });
});
function Ts(e, t, n) {
  return t == null || typeof t == "boolean" || t === "" ? "" : n || typeof t != "number" || t === 0 || Pn.hasOwnProperty(e) && Pn[e] ? ("" + t).trim() : t + "px";
}
function zs(e, t) {
  e = e.style;
  for (var n in t) if (t.hasOwnProperty(n)) {
    var r = n.indexOf("--") === 0, l = Ts(n, t[n], r);
    n === "float" && (n = "cssFloat"), r ? e.setProperty(n, l) : e[n] = l;
  }
}
var nf = Q({ menuitem: !0 }, { area: !0, base: !0, br: !0, col: !0, embed: !0, hr: !0, img: !0, input: !0, keygen: !0, link: !0, meta: !0, param: !0, source: !0, track: !0, wbr: !0 });
function fo(e, t) {
  if (t) {
    if (nf[e] && (t.children != null || t.dangerouslySetInnerHTML != null)) throw Error(k(137, e));
    if (t.dangerouslySetInnerHTML != null) {
      if (t.children != null) throw Error(k(60));
      if (typeof t.dangerouslySetInnerHTML != "object" || !("__html" in t.dangerouslySetInnerHTML)) throw Error(k(61));
    }
    if (t.style != null && typeof t.style != "object") throw Error(k(62));
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
    if (typeof ho != "function") throw Error(k(280));
    var t = e.stateNode;
    t && (t = dl(t), ho(e.stateNode, e.type, t));
  }
}
function Rs(e) {
  Zt ? Jt ? Jt.push(e) : Jt = [e] : Zt = e;
}
function Ms() {
  if (Zt) {
    var e = Zt, t = Jt;
    if (Jt = Zt = null, eu(e), t) for (e = 0; e < t.length; e++) eu(t[e]);
  }
}
function Is(e, t) {
  return e(t);
}
function Ds() {
}
var Ll = !1;
function Os(e, t, n) {
  if (Ll) return e(t, n);
  Ll = !0;
  try {
    return Is(e, t, n);
  } finally {
    Ll = !1, (Zt !== null || Jt !== null) && (Ds(), Ms());
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
  if (n && typeof n != "function") throw Error(k(231, t, typeof n));
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
function rf(e, t, n, r, l, o, i, u, s) {
  var c = Array.prototype.slice.call(arguments, 3);
  try {
    t.apply(n, c);
  } catch (m) {
    this.onError(m);
  }
}
var jn = !1, Ur = null, Ar = !1, go = null, lf = { onError: function(e) {
  jn = !0, Ur = e;
} };
function of(e, t, n, r, l, o, i, u, s) {
  jn = !1, Ur = null, rf.apply(lf, arguments);
}
function uf(e, t, n, r, l, o, i, u, s) {
  if (of.apply(this, arguments), jn) {
    if (jn) {
      var c = Ur;
      jn = !1, Ur = null;
    } else throw Error(k(198));
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
function Fs(e) {
  if (e.tag === 13) {
    var t = e.memoizedState;
    if (t === null && (e = e.alternate, e !== null && (t = e.memoizedState)), t !== null) return t.dehydrated;
  }
  return null;
}
function tu(e) {
  if (Dt(e) !== e) throw Error(k(188));
}
function sf(e) {
  var t = e.alternate;
  if (!t) {
    if (t = Dt(e), t === null) throw Error(k(188));
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
      throw Error(k(188));
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
        if (!i) throw Error(k(189));
      }
    }
    if (n.alternate !== r) throw Error(k(190));
  }
  if (n.tag !== 3) throw Error(k(188));
  return n.stateNode.current === n ? e : t;
}
function Us(e) {
  return e = sf(e), e !== null ? As(e) : null;
}
function As(e) {
  if (e.tag === 5 || e.tag === 6) return e;
  for (e = e.child; e !== null; ) {
    var t = As(e);
    if (t !== null) return t;
    e = e.sibling;
  }
  return null;
}
var Vs = ke.unstable_scheduleCallback, nu = ke.unstable_cancelCallback, af = ke.unstable_shouldYield, cf = ke.unstable_requestPaint, X = ke.unstable_now, ff = ke.unstable_getCurrentPriorityLevel, oi = ke.unstable_ImmediatePriority, $s = ke.unstable_UserBlockingPriority, Vr = ke.unstable_NormalPriority, df = ke.unstable_LowPriority, Bs = ke.unstable_IdlePriority, sl = null, Be = null;
function pf(e) {
  if (Be && typeof Be.onCommitFiberRoot == "function") try {
    Be.onCommitFiberRoot(sl, e, void 0, (e.current.flags & 128) === 128);
  } catch {
  }
}
var Oe = Math.clz32 ? Math.clz32 : vf, mf = Math.log, hf = Math.LN2;
function vf(e) {
  return e >>>= 0, e === 0 ? 32 : 31 - (mf(e) / hf | 0) | 0;
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
function gf(e, t) {
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
function yf(e, t) {
  for (var n = e.suspendedLanes, r = e.pingedLanes, l = e.expirationTimes, o = e.pendingLanes; 0 < o; ) {
    var i = 31 - Oe(o), u = 1 << i, s = l[i];
    s === -1 ? (!(u & n) || u & r) && (l[i] = gf(u, t)) : s <= t && (e.expiredLanes |= u), o &= ~u;
  }
}
function yo(e) {
  return e = e.pendingLanes & -1073741825, e !== 0 ? e : e & 1073741824 ? 1073741824 : 0;
}
function Hs() {
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
function wf(e, t) {
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
var O = 0;
function Qs(e) {
  return e &= -e, 1 < e ? 4 < e ? e & 268435455 ? 16 : 536870912 : 4 : 1;
}
var Ws, ui, Ks, Ys, Xs, wo = !1, mr = [], ut = null, st = null, at = null, An = /* @__PURE__ */ new Map(), Vn = /* @__PURE__ */ new Map(), rt = [], kf = "mousedown mouseup touchcancel touchend touchstart auxclick dblclick pointercancel pointerdown pointerup dragend dragstart drop compositionend compositionstart keydown keypress keyup input textInput copy cut paste click change contextmenu reset submit".split(" ");
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
function Sf(e, t, n, r, l) {
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
function Gs(e) {
  var t = Ct(e.target);
  if (t !== null) {
    var n = Dt(t);
    if (n !== null) {
      if (t = n.tag, t === 13) {
        if (t = Fs(n), t !== null) {
          e.blockedOn = t, Xs(e.priority, function() {
            Ks(n);
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
function xf() {
  wo = !1, ut !== null && Pr(ut) && (ut = null), st !== null && Pr(st) && (st = null), at !== null && Pr(at) && (at = null), An.forEach(lu), Vn.forEach(lu);
}
function yn(e, t) {
  e.blockedOn === t && (e.blockedOn = null, wo || (wo = !0, ke.unstable_scheduleCallback(ke.unstable_NormalPriority, xf)));
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
  for (; 0 < rt.length && (n = rt[0], n.blockedOn === null); ) Gs(n), n.blockedOn === null && rt.shift();
}
var qt = be.ReactCurrentBatchConfig, Br = !0;
function Ef(e, t, n, r) {
  var l = O, o = qt.transition;
  qt.transition = null;
  try {
    O = 1, si(e, t, n, r);
  } finally {
    O = l, qt.transition = o;
  }
}
function Cf(e, t, n, r) {
  var l = O, o = qt.transition;
  qt.transition = null;
  try {
    O = 4, si(e, t, n, r);
  } finally {
    O = l, qt.transition = o;
  }
}
function si(e, t, n, r) {
  if (Br) {
    var l = ko(e, t, n, r);
    if (l === null) Vl(e, t, r, Hr, n), ru(e, r);
    else if (Sf(l, e, t, n, r)) r.stopPropagation();
    else if (ru(e, r), t & 4 && -1 < kf.indexOf(e)) {
      for (; l !== null; ) {
        var o = rr(l);
        if (o !== null && Ws(o), o = ko(e, t, n, r), o === null && Vl(e, t, r, Hr, n), o === l) break;
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
    if (e = Fs(t), e !== null) return e;
    e = null;
  } else if (n === 3) {
    if (t.stateNode.current.memoizedState.isDehydrated) return t.tag === 3 ? t.stateNode.containerInfo : null;
    e = null;
  } else t !== e && (e = null);
  return Hr = e, null;
}
function Zs(e) {
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
      switch (ff()) {
        case oi:
          return 1;
        case $s:
          return 4;
        case Vr:
        case df:
          return 16;
        case Bs:
          return 536870912;
        default:
          return 16;
      }
    default:
      return 16;
  }
}
var ot = null, ai = null, jr = null;
function Js() {
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
function xe(e) {
  function t(n, r, l, o, i) {
    this._reactName = n, this._targetInst = l, this.type = r, this.nativeEvent = o, this.target = i, this.currentTarget = null;
    for (var u in e) e.hasOwnProperty(u) && (n = e[u], this[u] = n ? n(o) : o[u]);
    return this.isDefaultPrevented = (o.defaultPrevented != null ? o.defaultPrevented : o.returnValue === !1) ? hr : ou, this.isPropagationStopped = ou, this;
  }
  return Q(t.prototype, { preventDefault: function() {
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
}, defaultPrevented: 0, isTrusted: 0 }, ci = xe(cn), nr = Q({}, cn, { view: 0, detail: 0 }), _f = xe(nr), zl, Rl, wn, al = Q({}, nr, { screenX: 0, screenY: 0, clientX: 0, clientY: 0, pageX: 0, pageY: 0, ctrlKey: 0, shiftKey: 0, altKey: 0, metaKey: 0, getModifierState: fi, button: 0, buttons: 0, relatedTarget: function(e) {
  return e.relatedTarget === void 0 ? e.fromElement === e.srcElement ? e.toElement : e.fromElement : e.relatedTarget;
}, movementX: function(e) {
  return "movementX" in e ? e.movementX : (e !== wn && (wn && e.type === "mousemove" ? (zl = e.screenX - wn.screenX, Rl = e.screenY - wn.screenY) : Rl = zl = 0, wn = e), zl);
}, movementY: function(e) {
  return "movementY" in e ? e.movementY : Rl;
} }), iu = xe(al), Nf = Q({}, al, { dataTransfer: 0 }), Pf = xe(Nf), jf = Q({}, nr, { relatedTarget: 0 }), Ml = xe(jf), Lf = Q({}, cn, { animationName: 0, elapsedTime: 0, pseudoElement: 0 }), Tf = xe(Lf), zf = Q({}, cn, { clipboardData: function(e) {
  return "clipboardData" in e ? e.clipboardData : window.clipboardData;
} }), Rf = xe(zf), Mf = Q({}, cn, { data: 0 }), uu = xe(Mf), If = {
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
}, Df = {
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
}, Of = { Alt: "altKey", Control: "ctrlKey", Meta: "metaKey", Shift: "shiftKey" };
function Ff(e) {
  var t = this.nativeEvent;
  return t.getModifierState ? t.getModifierState(e) : (e = Of[e]) ? !!t[e] : !1;
}
function fi() {
  return Ff;
}
var Uf = Q({}, nr, { key: function(e) {
  if (e.key) {
    var t = If[e.key] || e.key;
    if (t !== "Unidentified") return t;
  }
  return e.type === "keypress" ? (e = Lr(e), e === 13 ? "Enter" : String.fromCharCode(e)) : e.type === "keydown" || e.type === "keyup" ? Df[e.keyCode] || "Unidentified" : "";
}, code: 0, location: 0, ctrlKey: 0, shiftKey: 0, altKey: 0, metaKey: 0, repeat: 0, locale: 0, getModifierState: fi, charCode: function(e) {
  return e.type === "keypress" ? Lr(e) : 0;
}, keyCode: function(e) {
  return e.type === "keydown" || e.type === "keyup" ? e.keyCode : 0;
}, which: function(e) {
  return e.type === "keypress" ? Lr(e) : e.type === "keydown" || e.type === "keyup" ? e.keyCode : 0;
} }), Af = xe(Uf), Vf = Q({}, al, { pointerId: 0, width: 0, height: 0, pressure: 0, tangentialPressure: 0, tiltX: 0, tiltY: 0, twist: 0, pointerType: 0, isPrimary: 0 }), su = xe(Vf), $f = Q({}, nr, { touches: 0, targetTouches: 0, changedTouches: 0, altKey: 0, metaKey: 0, ctrlKey: 0, shiftKey: 0, getModifierState: fi }), Bf = xe($f), Hf = Q({}, cn, { propertyName: 0, elapsedTime: 0, pseudoElement: 0 }), Qf = xe(Hf), Wf = Q({}, al, {
  deltaX: function(e) {
    return "deltaX" in e ? e.deltaX : "wheelDeltaX" in e ? -e.wheelDeltaX : 0;
  },
  deltaY: function(e) {
    return "deltaY" in e ? e.deltaY : "wheelDeltaY" in e ? -e.wheelDeltaY : "wheelDelta" in e ? -e.wheelDelta : 0;
  },
  deltaZ: 0,
  deltaMode: 0
}), Kf = xe(Wf), Yf = [9, 13, 27, 32], di = Ge && "CompositionEvent" in window, Ln = null;
Ge && "documentMode" in document && (Ln = document.documentMode);
var Xf = Ge && "TextEvent" in window && !Ln, qs = Ge && (!di || Ln && 8 < Ln && 11 >= Ln), au = " ", cu = !1;
function bs(e, t) {
  switch (e) {
    case "keyup":
      return Yf.indexOf(t.keyCode) !== -1;
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
function ea(e) {
  return e = e.detail, typeof e == "object" && "data" in e ? e.data : null;
}
var At = !1;
function Gf(e, t) {
  switch (e) {
    case "compositionend":
      return ea(t);
    case "keypress":
      return t.which !== 32 ? null : (cu = !0, au);
    case "textInput":
      return e = t.data, e === au && cu ? null : e;
    default:
      return null;
  }
}
function Zf(e, t) {
  if (At) return e === "compositionend" || !di && bs(e, t) ? (e = Js(), jr = ai = ot = null, At = !1, e) : null;
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
      return qs && t.locale !== "ko" ? null : t.data;
    default:
      return null;
  }
}
var Jf = { color: !0, date: !0, datetime: !0, "datetime-local": !0, email: !0, month: !0, number: !0, password: !0, range: !0, search: !0, tel: !0, text: !0, time: !0, url: !0, week: !0 };
function fu(e) {
  var t = e && e.nodeName && e.nodeName.toLowerCase();
  return t === "input" ? !!Jf[e.type] : t === "textarea";
}
function ta(e, t, n, r) {
  Rs(r), t = Qr(t, "onChange"), 0 < t.length && (n = new ci("onChange", "change", null, n, r), e.push({ event: n, listeners: t }));
}
var Tn = null, Bn = null;
function qf(e) {
  da(e, 0);
}
function cl(e) {
  var t = Bt(e);
  if (_s(t)) return e;
}
function bf(e, t) {
  if (e === "change") return t;
}
var na = !1;
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
  na = Il && (!document.documentMode || 9 < document.documentMode);
}
function pu() {
  Tn && (Tn.detachEvent("onpropertychange", ra), Bn = Tn = null);
}
function ra(e) {
  if (e.propertyName === "value" && cl(Bn)) {
    var t = [];
    ta(t, Bn, e, li(e)), Os(qf, t);
  }
}
function ed(e, t, n) {
  e === "focusin" ? (pu(), Tn = t, Bn = n, Tn.attachEvent("onpropertychange", ra)) : e === "focusout" && pu();
}
function td(e) {
  if (e === "selectionchange" || e === "keyup" || e === "keydown") return cl(Bn);
}
function nd(e, t) {
  if (e === "click") return cl(t);
}
function rd(e, t) {
  if (e === "input" || e === "change") return cl(t);
}
function ld(e, t) {
  return e === t && (e !== 0 || 1 / e === 1 / t) || e !== e && t !== t;
}
var Ue = typeof Object.is == "function" ? Object.is : ld;
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
function la(e, t) {
  return e && t ? e === t ? !0 : e && e.nodeType === 3 ? !1 : t && t.nodeType === 3 ? la(e, t.parentNode) : "contains" in e ? e.contains(t) : e.compareDocumentPosition ? !!(e.compareDocumentPosition(t) & 16) : !1 : !1;
}
function oa() {
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
function od(e) {
  var t = oa(), n = e.focusedElem, r = e.selectionRange;
  if (t !== n && n && n.ownerDocument && la(n.ownerDocument.documentElement, n)) {
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
var id = Ge && "documentMode" in document && 11 >= document.documentMode, Vt = null, So = null, zn = null, xo = !1;
function vu(e, t, n) {
  var r = n.window === n ? n.document : n.nodeType === 9 ? n : n.ownerDocument;
  xo || Vt == null || Vt !== Fr(r) || (r = Vt, "selectionStart" in r && pi(r) ? r = { start: r.selectionStart, end: r.selectionEnd } : (r = (r.ownerDocument && r.ownerDocument.defaultView || window).getSelection(), r = { anchorNode: r.anchorNode, anchorOffset: r.anchorOffset, focusNode: r.focusNode, focusOffset: r.focusOffset }), zn && Hn(zn, r) || (zn = r, r = Qr(So, "onSelect"), 0 < r.length && (t = new ci("onSelect", "select", null, t, n), e.push({ event: t, listeners: r }), t.target = Vt)));
}
function vr(e, t) {
  var n = {};
  return n[e.toLowerCase()] = t.toLowerCase(), n["Webkit" + e] = "webkit" + t, n["Moz" + e] = "moz" + t, n;
}
var $t = { animationend: vr("Animation", "AnimationEnd"), animationiteration: vr("Animation", "AnimationIteration"), animationstart: vr("Animation", "AnimationStart"), transitionend: vr("Transition", "TransitionEnd") }, Ol = {}, ia = {};
Ge && (ia = document.createElement("div").style, "AnimationEvent" in window || (delete $t.animationend.animation, delete $t.animationiteration.animation, delete $t.animationstart.animation), "TransitionEvent" in window || delete $t.transitionend.transition);
function fl(e) {
  if (Ol[e]) return Ol[e];
  if (!$t[e]) return e;
  var t = $t[e], n;
  for (n in t) if (t.hasOwnProperty(n) && n in ia) return Ol[e] = t[n];
  return e;
}
var ua = fl("animationend"), sa = fl("animationiteration"), aa = fl("animationstart"), ca = fl("transitionend"), fa = /* @__PURE__ */ new Map(), gu = "abort auxClick cancel canPlay canPlayThrough click close contextMenu copy cut drag dragEnd dragEnter dragExit dragLeave dragOver dragStart drop durationChange emptied encrypted ended error gotPointerCapture input invalid keyDown keyPress keyUp load loadedData loadedMetadata loadStart lostPointerCapture mouseDown mouseMove mouseOut mouseOver mouseUp paste pause play playing pointerCancel pointerDown pointerMove pointerOut pointerOver pointerUp progress rateChange reset resize seeked seeking stalled submit suspend timeUpdate touchCancel touchEnd touchStart volumeChange scroll toggle touchMove waiting wheel".split(" ");
function gt(e, t) {
  fa.set(e, t), It(t, [e]);
}
for (var Fl = 0; Fl < gu.length; Fl++) {
  var Ul = gu[Fl], ud = Ul.toLowerCase(), sd = Ul[0].toUpperCase() + Ul.slice(1);
  gt(ud, "on" + sd);
}
gt(ua, "onAnimationEnd");
gt(sa, "onAnimationIteration");
gt(aa, "onAnimationStart");
gt("dblclick", "onDoubleClick");
gt("focusin", "onFocus");
gt("focusout", "onBlur");
gt(ca, "onTransitionEnd");
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
var Nn = "abort canplay canplaythrough durationchange emptied encrypted ended error loadeddata loadedmetadata loadstart pause play playing progress ratechange resize seeked seeking stalled suspend timeupdate volumechange waiting".split(" "), ad = new Set("cancel close invalid load scroll toggle".split(" ").concat(Nn));
function yu(e, t, n) {
  var r = e.type || "unknown-event";
  e.currentTarget = n, uf(r, t, void 0, e), e.currentTarget = null;
}
function da(e, t) {
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
function U(e, t) {
  var n = t[Po];
  n === void 0 && (n = t[Po] = /* @__PURE__ */ new Set());
  var r = e + "__bubble";
  n.has(r) || (pa(t, e, 2, !1), n.add(r));
}
function Al(e, t, n) {
  var r = 0;
  t && (r |= 4), pa(n, e, r, t);
}
var gr = "_reactListening" + Math.random().toString(36).slice(2);
function Qn(e) {
  if (!e[gr]) {
    e[gr] = !0, ks.forEach(function(n) {
      n !== "selectionchange" && (ad.has(n) || Al(n, !1, e), Al(n, !0, e));
    });
    var t = e.nodeType === 9 ? e : e.ownerDocument;
    t === null || t[gr] || (t[gr] = !0, Al("selectionchange", !1, t));
  }
}
function pa(e, t, n, r) {
  switch (Zs(t)) {
    case 1:
      var l = Ef;
      break;
    case 4:
      l = Cf;
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
  Os(function() {
    var c = o, m = li(n), h = [];
    e: {
      var d = fa.get(e);
      if (d !== void 0) {
        var w = ci, C = e;
        switch (e) {
          case "keypress":
            if (Lr(n) === 0) break e;
          case "keydown":
          case "keyup":
            w = Af;
            break;
          case "focusin":
            C = "focus", w = Ml;
            break;
          case "focusout":
            C = "blur", w = Ml;
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
            w = Pf;
            break;
          case "touchcancel":
          case "touchend":
          case "touchmove":
          case "touchstart":
            w = Bf;
            break;
          case ua:
          case sa:
          case aa:
            w = Tf;
            break;
          case ca:
            w = Qf;
            break;
          case "scroll":
            w = _f;
            break;
          case "wheel":
            w = Kf;
            break;
          case "copy":
          case "cut":
          case "paste":
            w = Rf;
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
        var S = (t & 4) !== 0, z = !S && e === "scroll", f = S ? d !== null ? d + "Capture" : null : d;
        S = [];
        for (var a = c, p; a !== null; ) {
          p = a;
          var y = p.stateNode;
          if (p.tag === 5 && y !== null && (p = y, f !== null && (y = Un(a, f), y != null && S.push(Wn(a, y, p)))), z) break;
          a = a.return;
        }
        0 < S.length && (d = new w(d, C, null, n, m), h.push({ event: d, listeners: S }));
      }
    }
    if (!(t & 7)) {
      e: {
        if (d = e === "mouseover" || e === "pointerover", w = e === "mouseout" || e === "pointerout", d && n !== mo && (C = n.relatedTarget || n.fromElement) && (Ct(C) || C[Ze])) break e;
        if ((w || d) && (d = m.window === m ? m : (d = m.ownerDocument) ? d.defaultView || d.parentWindow : window, w ? (C = n.relatedTarget || n.toElement, w = c, C = C ? Ct(C) : null, C !== null && (z = Dt(C), C !== z || C.tag !== 5 && C.tag !== 6) && (C = null)) : (w = null, C = c), w !== C)) {
          if (S = iu, y = "onMouseLeave", f = "onMouseEnter", a = "mouse", (e === "pointerout" || e === "pointerover") && (S = su, y = "onPointerLeave", f = "onPointerEnter", a = "pointer"), z = w == null ? d : Bt(w), p = C == null ? d : Bt(C), d = new S(y, a + "leave", w, n, m), d.target = z, d.relatedTarget = p, y = null, Ct(m) === c && (S = new S(f, a + "enter", C, n, m), S.target = p, S.relatedTarget = z, y = S), z = y, w && C) t: {
            for (S = w, f = C, a = 0, p = S; p; p = Ot(p)) a++;
            for (p = 0, y = f; y; y = Ot(y)) p++;
            for (; 0 < a - p; ) S = Ot(S), a--;
            for (; 0 < p - a; ) f = Ot(f), p--;
            for (; a--; ) {
              if (S === f || f !== null && S === f.alternate) break t;
              S = Ot(S), f = Ot(f);
            }
            S = null;
          }
          else S = null;
          w !== null && wu(h, d, w, S, !1), C !== null && z !== null && wu(h, z, C, S, !0);
        }
      }
      e: {
        if (d = c ? Bt(c) : window, w = d.nodeName && d.nodeName.toLowerCase(), w === "select" || w === "input" && d.type === "file") var _ = bf;
        else if (fu(d)) if (na) _ = rd;
        else {
          _ = td;
          var N = ed;
        }
        else (w = d.nodeName) && w.toLowerCase() === "input" && (d.type === "checkbox" || d.type === "radio") && (_ = nd);
        if (_ && (_ = _(e, c))) {
          ta(h, _, n, m);
          break e;
        }
        N && N(e, d, c), e === "focusout" && (N = d._wrapperState) && N.controlled && d.type === "number" && so(d, "number", d.value);
      }
      switch (N = c ? Bt(c) : window, e) {
        case "focusin":
          (fu(N) || N.contentEditable === "true") && (Vt = N, So = c, zn = null);
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
          xo = !1, vu(h, n, m);
          break;
        case "selectionchange":
          if (id) break;
        case "keydown":
        case "keyup":
          vu(h, n, m);
      }
      var x;
      if (di) e: {
        switch (e) {
          case "compositionstart":
            var g = "onCompositionStart";
            break e;
          case "compositionend":
            g = "onCompositionEnd";
            break e;
          case "compositionupdate":
            g = "onCompositionUpdate";
            break e;
        }
        g = void 0;
      }
      else At ? bs(e, n) && (g = "onCompositionEnd") : e === "keydown" && n.keyCode === 229 && (g = "onCompositionStart");
      g && (qs && n.locale !== "ko" && (At || g !== "onCompositionStart" ? g === "onCompositionEnd" && At && (x = Js()) : (ot = m, ai = "value" in ot ? ot.value : ot.textContent, At = !0)), N = Qr(c, g), 0 < N.length && (g = new uu(g, e, null, n, m), h.push({ event: g, listeners: N }), x ? g.data = x : (x = ea(n), x !== null && (g.data = x)))), (x = Xf ? Gf(e, n) : Zf(e, n)) && (c = Qr(c, "onBeforeInput"), 0 < c.length && (m = new uu("onBeforeInput", "beforeinput", null, n, m), h.push({ event: m, listeners: c }), m.data = x));
    }
    da(h, t);
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
var cd = /\r\n?/g, fd = /\u0000|\uFFFD/g;
function ku(e) {
  return (typeof e == "string" ? e : "" + e).replace(cd, `
`).replace(fd, "");
}
function yr(e, t, n) {
  if (t = ku(t), ku(e) !== t && n) throw Error(k(425));
}
function Wr() {
}
var Eo = null, Co = null;
function _o(e, t) {
  return e === "textarea" || e === "noscript" || typeof t.children == "string" || typeof t.children == "number" || typeof t.dangerouslySetInnerHTML == "object" && t.dangerouslySetInnerHTML !== null && t.dangerouslySetInnerHTML.__html != null;
}
var No = typeof setTimeout == "function" ? setTimeout : void 0, dd = typeof clearTimeout == "function" ? clearTimeout : void 0, Su = typeof Promise == "function" ? Promise : void 0, pd = typeof queueMicrotask == "function" ? queueMicrotask : typeof Su < "u" ? function(e) {
  return Su.resolve(null).then(e).catch(md);
} : No;
function md(e) {
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
var fn = Math.random().toString(36).slice(2), $e = "__reactFiber$" + fn, Kn = "__reactProps$" + fn, Ze = "__reactContainer$" + fn, Po = "__reactEvents$" + fn, hd = "__reactListeners$" + fn, vd = "__reactHandles$" + fn;
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
  throw Error(k(33));
}
function dl(e) {
  return e[Kn] || null;
}
var jo = [], Ht = -1;
function yt(e) {
  return { current: e };
}
function A(e) {
  0 > Ht || (e.current = jo[Ht], jo[Ht] = null, Ht--);
}
function F(e, t) {
  Ht++, jo[Ht] = e.current, e.current = t;
}
var vt = {}, ue = yt(vt), me = yt(!1), Lt = vt;
function nn(e, t) {
  var n = e.type.contextTypes;
  if (!n) return vt;
  var r = e.stateNode;
  if (r && r.__reactInternalMemoizedUnmaskedChildContext === t) return r.__reactInternalMemoizedMaskedChildContext;
  var l = {}, o;
  for (o in n) l[o] = t[o];
  return r && (e = e.stateNode, e.__reactInternalMemoizedUnmaskedChildContext = t, e.__reactInternalMemoizedMaskedChildContext = l), l;
}
function he(e) {
  return e = e.childContextTypes, e != null;
}
function Kr() {
  A(me), A(ue);
}
function Eu(e, t, n) {
  if (ue.current !== vt) throw Error(k(168));
  F(ue, t), F(me, n);
}
function ma(e, t, n) {
  var r = e.stateNode;
  if (t = t.childContextTypes, typeof r.getChildContext != "function") return n;
  r = r.getChildContext();
  for (var l in r) if (!(l in t)) throw Error(k(108, bc(e) || "Unknown", l));
  return Q({}, n, r);
}
function Yr(e) {
  return e = (e = e.stateNode) && e.__reactInternalMemoizedMergedChildContext || vt, Lt = ue.current, F(ue, e), F(me, me.current), !0;
}
function Cu(e, t, n) {
  var r = e.stateNode;
  if (!r) throw Error(k(169));
  n ? (e = ma(e, t, Lt), r.__reactInternalMemoizedMergedChildContext = e, A(me), A(ue), F(ue, e)) : A(me), F(me, n);
}
var We = null, pl = !1, Bl = !1;
function ha(e) {
  We === null ? We = [e] : We.push(e);
}
function gd(e) {
  pl = !0, ha(e);
}
function wt() {
  if (!Bl && We !== null) {
    Bl = !0;
    var e = 0, t = O;
    try {
      var n = We;
      for (O = 1; e < n.length; e++) {
        var r = n[e];
        do
          r = r(!0);
        while (r !== null);
      }
      We = null, pl = !1;
    } catch (l) {
      throw We !== null && (We = We.slice(e + 1)), Vs(oi, wt), l;
    } finally {
      O = t, Bl = !1;
    }
  }
  return null;
}
var Qt = [], Wt = 0, Xr = null, Gr = 0, _e = [], Ne = 0, Tt = null, Ke = 1, Ye = "";
function xt(e, t) {
  Qt[Wt++] = Gr, Qt[Wt++] = Xr, Xr = e, Gr = t;
}
function va(e, t, n) {
  _e[Ne++] = Ke, _e[Ne++] = Ye, _e[Ne++] = Tt, Tt = e;
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
  e.return !== null && (xt(e, 1), va(e, 1, 0));
}
function hi(e) {
  for (; e === Xr; ) Xr = Qt[--Wt], Qt[Wt] = null, Gr = Qt[--Wt], Qt[Wt] = null;
  for (; e === Tt; ) Tt = _e[--Ne], _e[Ne] = null, Ye = _e[--Ne], _e[Ne] = null, Ke = _e[--Ne], _e[Ne] = null;
}
var we = null, ye = null, V = !1, De = null;
function ga(e, t) {
  var n = Pe(5, null, null, 0);
  n.elementType = "DELETED", n.stateNode = t, n.return = e, t = e.deletions, t === null ? (e.deletions = [n], e.flags |= 16) : t.push(n);
}
function _u(e, t) {
  switch (e.tag) {
    case 5:
      var n = e.type;
      return t = t.nodeType !== 1 || n.toLowerCase() !== t.nodeName.toLowerCase() ? null : t, t !== null ? (e.stateNode = t, we = e, ye = ct(t.firstChild), !0) : !1;
    case 6:
      return t = e.pendingProps === "" || t.nodeType !== 3 ? null : t, t !== null ? (e.stateNode = t, we = e, ye = null, !0) : !1;
    case 13:
      return t = t.nodeType !== 8 ? null : t, t !== null ? (n = Tt !== null ? { id: Ke, overflow: Ye } : null, e.memoizedState = { dehydrated: t, treeContext: n, retryLane: 1073741824 }, n = Pe(18, null, null, 0), n.stateNode = t, n.return = e, e.child = n, we = e, ye = null, !0) : !1;
    default:
      return !1;
  }
}
function Lo(e) {
  return (e.mode & 1) !== 0 && (e.flags & 128) === 0;
}
function To(e) {
  if (V) {
    var t = ye;
    if (t) {
      var n = t;
      if (!_u(e, t)) {
        if (Lo(e)) throw Error(k(418));
        t = ct(n.nextSibling);
        var r = we;
        t && _u(e, t) ? ga(r, n) : (e.flags = e.flags & -4097 | 2, V = !1, we = e);
      }
    } else {
      if (Lo(e)) throw Error(k(418));
      e.flags = e.flags & -4097 | 2, V = !1, we = e;
    }
  }
}
function Nu(e) {
  for (e = e.return; e !== null && e.tag !== 5 && e.tag !== 3 && e.tag !== 13; ) e = e.return;
  we = e;
}
function wr(e) {
  if (e !== we) return !1;
  if (!V) return Nu(e), V = !0, !1;
  var t;
  if ((t = e.tag !== 3) && !(t = e.tag !== 5) && (t = e.type, t = t !== "head" && t !== "body" && !_o(e.type, e.memoizedProps)), t && (t = ye)) {
    if (Lo(e)) throw ya(), Error(k(418));
    for (; t; ) ga(e, t), t = ct(t.nextSibling);
  }
  if (Nu(e), e.tag === 13) {
    if (e = e.memoizedState, e = e !== null ? e.dehydrated : null, !e) throw Error(k(317));
    e: {
      for (e = e.nextSibling, t = 0; e; ) {
        if (e.nodeType === 8) {
          var n = e.data;
          if (n === "/$") {
            if (t === 0) {
              ye = ct(e.nextSibling);
              break e;
            }
            t--;
          } else n !== "$" && n !== "$!" && n !== "$?" || t++;
        }
        e = e.nextSibling;
      }
      ye = null;
    }
  } else ye = we ? ct(e.stateNode.nextSibling) : null;
  return !0;
}
function ya() {
  for (var e = ye; e; ) e = ct(e.nextSibling);
}
function rn() {
  ye = we = null, V = !1;
}
function vi(e) {
  De === null ? De = [e] : De.push(e);
}
var yd = be.ReactCurrentBatchConfig;
function kn(e, t, n) {
  if (e = n.ref, e !== null && typeof e != "function" && typeof e != "object") {
    if (n._owner) {
      if (n = n._owner, n) {
        if (n.tag !== 1) throw Error(k(309));
        var r = n.stateNode;
      }
      if (!r) throw Error(k(147, e));
      var l = r, o = "" + e;
      return t !== null && t.ref !== null && typeof t.ref == "function" && t.ref._stringRef === o ? t.ref : (t = function(i) {
        var u = l.refs;
        i === null ? delete u[o] : u[o] = i;
      }, t._stringRef = o, t);
    }
    if (typeof e != "string") throw Error(k(284));
    if (!n._owner) throw Error(k(290, e));
  }
  return e;
}
function kr(e, t) {
  throw e = Object.prototype.toString.call(t), Error(k(31, e === "[object Object]" ? "object with keys {" + Object.keys(t).join(", ") + "}" : e));
}
function Pu(e) {
  var t = e._init;
  return t(e._payload);
}
function wa(e) {
  function t(f, a) {
    if (e) {
      var p = f.deletions;
      p === null ? (f.deletions = [a], f.flags |= 16) : p.push(a);
    }
  }
  function n(f, a) {
    if (!e) return null;
    for (; a !== null; ) t(f, a), a = a.sibling;
    return null;
  }
  function r(f, a) {
    for (f = /* @__PURE__ */ new Map(); a !== null; ) a.key !== null ? f.set(a.key, a) : f.set(a.index, a), a = a.sibling;
    return f;
  }
  function l(f, a) {
    return f = mt(f, a), f.index = 0, f.sibling = null, f;
  }
  function o(f, a, p) {
    return f.index = p, e ? (p = f.alternate, p !== null ? (p = p.index, p < a ? (f.flags |= 2, a) : p) : (f.flags |= 2, a)) : (f.flags |= 1048576, a);
  }
  function i(f) {
    return e && f.alternate === null && (f.flags |= 2), f;
  }
  function u(f, a, p, y) {
    return a === null || a.tag !== 6 ? (a = Gl(p, f.mode, y), a.return = f, a) : (a = l(a, p), a.return = f, a);
  }
  function s(f, a, p, y) {
    var _ = p.type;
    return _ === Ut ? m(f, a, p.props.children, y, p.key) : a !== null && (a.elementType === _ || typeof _ == "object" && _ !== null && _.$$typeof === tt && Pu(_) === a.type) ? (y = l(a, p.props), y.ref = kn(f, a, p), y.return = f, y) : (y = Or(p.type, p.key, p.props, null, f.mode, y), y.ref = kn(f, a, p), y.return = f, y);
  }
  function c(f, a, p, y) {
    return a === null || a.tag !== 4 || a.stateNode.containerInfo !== p.containerInfo || a.stateNode.implementation !== p.implementation ? (a = Zl(p, f.mode, y), a.return = f, a) : (a = l(a, p.children || []), a.return = f, a);
  }
  function m(f, a, p, y, _) {
    return a === null || a.tag !== 7 ? (a = jt(p, f.mode, y, _), a.return = f, a) : (a = l(a, p), a.return = f, a);
  }
  function h(f, a, p) {
    if (typeof a == "string" && a !== "" || typeof a == "number") return a = Gl("" + a, f.mode, p), a.return = f, a;
    if (typeof a == "object" && a !== null) {
      switch (a.$$typeof) {
        case ar:
          return p = Or(a.type, a.key, a.props, null, f.mode, p), p.ref = kn(f, null, a), p.return = f, p;
        case Ft:
          return a = Zl(a, f.mode, p), a.return = f, a;
        case tt:
          var y = a._init;
          return h(f, y(a._payload), p);
      }
      if (Cn(a) || hn(a)) return a = jt(a, f.mode, p, null), a.return = f, a;
      kr(f, a);
    }
    return null;
  }
  function d(f, a, p, y) {
    var _ = a !== null ? a.key : null;
    if (typeof p == "string" && p !== "" || typeof p == "number") return _ !== null ? null : u(f, a, "" + p, y);
    if (typeof p == "object" && p !== null) {
      switch (p.$$typeof) {
        case ar:
          return p.key === _ ? s(f, a, p, y) : null;
        case Ft:
          return p.key === _ ? c(f, a, p, y) : null;
        case tt:
          return _ = p._init, d(
            f,
            a,
            _(p._payload),
            y
          );
      }
      if (Cn(p) || hn(p)) return _ !== null ? null : m(f, a, p, y, null);
      kr(f, p);
    }
    return null;
  }
  function w(f, a, p, y, _) {
    if (typeof y == "string" && y !== "" || typeof y == "number") return f = f.get(p) || null, u(a, f, "" + y, _);
    if (typeof y == "object" && y !== null) {
      switch (y.$$typeof) {
        case ar:
          return f = f.get(y.key === null ? p : y.key) || null, s(a, f, y, _);
        case Ft:
          return f = f.get(y.key === null ? p : y.key) || null, c(a, f, y, _);
        case tt:
          var N = y._init;
          return w(f, a, p, N(y._payload), _);
      }
      if (Cn(y) || hn(y)) return f = f.get(p) || null, m(a, f, y, _, null);
      kr(a, y);
    }
    return null;
  }
  function C(f, a, p, y) {
    for (var _ = null, N = null, x = a, g = a = 0, E = null; x !== null && g < p.length; g++) {
      x.index > g ? (E = x, x = null) : E = x.sibling;
      var L = d(f, x, p[g], y);
      if (L === null) {
        x === null && (x = E);
        break;
      }
      e && x && L.alternate === null && t(f, x), a = o(L, a, g), N === null ? _ = L : N.sibling = L, N = L, x = E;
    }
    if (g === p.length) return n(f, x), V && xt(f, g), _;
    if (x === null) {
      for (; g < p.length; g++) x = h(f, p[g], y), x !== null && (a = o(x, a, g), N === null ? _ = x : N.sibling = x, N = x);
      return V && xt(f, g), _;
    }
    for (x = r(f, x); g < p.length; g++) E = w(x, f, g, p[g], y), E !== null && (e && E.alternate !== null && x.delete(E.key === null ? g : E.key), a = o(E, a, g), N === null ? _ = E : N.sibling = E, N = E);
    return e && x.forEach(function(W) {
      return t(f, W);
    }), V && xt(f, g), _;
  }
  function S(f, a, p, y) {
    var _ = hn(p);
    if (typeof _ != "function") throw Error(k(150));
    if (p = _.call(p), p == null) throw Error(k(151));
    for (var N = _ = null, x = a, g = a = 0, E = null, L = p.next(); x !== null && !L.done; g++, L = p.next()) {
      x.index > g ? (E = x, x = null) : E = x.sibling;
      var W = d(f, x, L.value, y);
      if (W === null) {
        x === null && (x = E);
        break;
      }
      e && x && W.alternate === null && t(f, x), a = o(W, a, g), N === null ? _ = W : N.sibling = W, N = W, x = E;
    }
    if (L.done) return n(
      f,
      x
    ), V && xt(f, g), _;
    if (x === null) {
      for (; !L.done; g++, L = p.next()) L = h(f, L.value, y), L !== null && (a = o(L, a, g), N === null ? _ = L : N.sibling = L, N = L);
      return V && xt(f, g), _;
    }
    for (x = r(f, x); !L.done; g++, L = p.next()) L = w(x, f, g, L.value, y), L !== null && (e && L.alternate !== null && x.delete(L.key === null ? g : L.key), a = o(L, a, g), N === null ? _ = L : N.sibling = L, N = L);
    return e && x.forEach(function(Ee) {
      return t(f, Ee);
    }), V && xt(f, g), _;
  }
  function z(f, a, p, y) {
    if (typeof p == "object" && p !== null && p.type === Ut && p.key === null && (p = p.props.children), typeof p == "object" && p !== null) {
      switch (p.$$typeof) {
        case ar:
          e: {
            for (var _ = p.key, N = a; N !== null; ) {
              if (N.key === _) {
                if (_ = p.type, _ === Ut) {
                  if (N.tag === 7) {
                    n(f, N.sibling), a = l(N, p.props.children), a.return = f, f = a;
                    break e;
                  }
                } else if (N.elementType === _ || typeof _ == "object" && _ !== null && _.$$typeof === tt && Pu(_) === N.type) {
                  n(f, N.sibling), a = l(N, p.props), a.ref = kn(f, N, p), a.return = f, f = a;
                  break e;
                }
                n(f, N);
                break;
              } else t(f, N);
              N = N.sibling;
            }
            p.type === Ut ? (a = jt(p.props.children, f.mode, y, p.key), a.return = f, f = a) : (y = Or(p.type, p.key, p.props, null, f.mode, y), y.ref = kn(f, a, p), y.return = f, f = y);
          }
          return i(f);
        case Ft:
          e: {
            for (N = p.key; a !== null; ) {
              if (a.key === N) if (a.tag === 4 && a.stateNode.containerInfo === p.containerInfo && a.stateNode.implementation === p.implementation) {
                n(f, a.sibling), a = l(a, p.children || []), a.return = f, f = a;
                break e;
              } else {
                n(f, a);
                break;
              }
              else t(f, a);
              a = a.sibling;
            }
            a = Zl(p, f.mode, y), a.return = f, f = a;
          }
          return i(f);
        case tt:
          return N = p._init, z(f, a, N(p._payload), y);
      }
      if (Cn(p)) return C(f, a, p, y);
      if (hn(p)) return S(f, a, p, y);
      kr(f, p);
    }
    return typeof p == "string" && p !== "" || typeof p == "number" ? (p = "" + p, a !== null && a.tag === 6 ? (n(f, a.sibling), a = l(a, p), a.return = f, f = a) : (n(f, a), a = Gl(p, f.mode, y), a.return = f, f = a), i(f)) : n(f, a);
  }
  return z;
}
var ln = wa(!0), ka = wa(!1), Zr = yt(null), Jr = null, Kt = null, gi = null;
function yi() {
  gi = Kt = Jr = null;
}
function wi(e) {
  var t = Zr.current;
  A(Zr), e._currentValue = t;
}
function zo(e, t, n) {
  for (; e !== null; ) {
    var r = e.alternate;
    if ((e.childLanes & t) !== t ? (e.childLanes |= t, r !== null && (r.childLanes |= t)) : r !== null && (r.childLanes & t) !== t && (r.childLanes |= t), e === n) break;
    e = e.return;
  }
}
function bt(e, t) {
  Jr = e, gi = Kt = null, e = e.dependencies, e !== null && e.firstContext !== null && (e.lanes & t && (pe = !0), e.firstContext = null);
}
function Le(e) {
  var t = e._currentValue;
  if (gi !== e) if (e = { context: e, memoizedValue: t, next: null }, Kt === null) {
    if (Jr === null) throw Error(k(308));
    Kt = e, Jr.dependencies = { lanes: 0, firstContext: e };
  } else Kt = Kt.next = e;
  return t;
}
var _t = null;
function ki(e) {
  _t === null ? _t = [e] : _t.push(e);
}
function Sa(e, t, n, r) {
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
function xa(e, t) {
  e = e.updateQueue, t.updateQueue === e && (t.updateQueue = { baseState: e.baseState, firstBaseUpdate: e.firstBaseUpdate, lastBaseUpdate: e.lastBaseUpdate, shared: e.shared, effects: e.effects });
}
function Xe(e, t) {
  return { eventTime: e, lane: t, tag: 0, payload: null, callback: null, next: null };
}
function ft(e, t, n) {
  var r = e.updateQueue;
  if (r === null) return null;
  if (r = r.shared, I & 2) {
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
    var m = e.alternate;
    m !== null && (m = m.updateQueue, u = m.lastBaseUpdate, u !== i && (u === null ? m.firstBaseUpdate = c : u.next = c, m.lastBaseUpdate = s));
  }
  if (o !== null) {
    var h = l.baseState;
    i = 0, m = c = s = null, u = o;
    do {
      var d = u.lane, w = u.eventTime;
      if ((r & d) === d) {
        m !== null && (m = m.next = {
          eventTime: w,
          lane: 0,
          tag: u.tag,
          payload: u.payload,
          callback: u.callback,
          next: null
        });
        e: {
          var C = e, S = u;
          switch (d = t, w = n, S.tag) {
            case 1:
              if (C = S.payload, typeof C == "function") {
                h = C.call(w, h, d);
                break e;
              }
              h = C;
              break e;
            case 3:
              C.flags = C.flags & -65537 | 128;
            case 0:
              if (C = S.payload, d = typeof C == "function" ? C.call(w, h, d) : C, d == null) break e;
              h = Q({}, h, d);
              break e;
            case 2:
              nt = !0;
          }
        }
        u.callback !== null && u.lane !== 0 && (e.flags |= 64, d = l.effects, d === null ? l.effects = [u] : d.push(u));
      } else w = { eventTime: w, lane: d, tag: u.tag, payload: u.payload, callback: u.callback, next: null }, m === null ? (c = m = w, s = h) : m = m.next = w, i |= d;
      if (u = u.next, u === null) {
        if (u = l.shared.pending, u === null) break;
        d = u, u = d.next, d.next = null, l.lastBaseUpdate = d, l.shared.pending = null;
      }
    } while (!0);
    if (m === null && (s = h), l.baseState = s, l.firstBaseUpdate = c, l.lastBaseUpdate = m, t = l.shared.interleaved, t !== null) {
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
      if (r.callback = null, r = n, typeof l != "function") throw Error(k(191, l));
      l.call(r);
    }
  }
}
var lr = {}, He = yt(lr), Yn = yt(lr), Xn = yt(lr);
function Nt(e) {
  if (e === lr) throw Error(k(174));
  return e;
}
function xi(e, t) {
  switch (F(Xn, t), F(Yn, e), F(He, lr), e = t.nodeType, e) {
    case 9:
    case 11:
      t = (t = t.documentElement) ? t.namespaceURI : co(null, "");
      break;
    default:
      e = e === 8 ? t.parentNode : t, t = e.namespaceURI || null, e = e.tagName, t = co(t, e);
  }
  A(He), F(He, t);
}
function on() {
  A(He), A(Yn), A(Xn);
}
function Ea(e) {
  Nt(Xn.current);
  var t = Nt(He.current), n = co(t, e.type);
  t !== n && (F(Yn, e), F(He, n));
}
function Ei(e) {
  Yn.current === e && (A(He), A(Yn));
}
var B = yt(0);
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
var zr = be.ReactCurrentDispatcher, Ql = be.ReactCurrentBatchConfig, zt = 0, H = null, Z = null, b = null, el = !1, Rn = !1, Gn = 0, wd = 0;
function le() {
  throw Error(k(321));
}
function _i(e, t) {
  if (t === null) return !1;
  for (var n = 0; n < t.length && n < e.length; n++) if (!Ue(e[n], t[n])) return !1;
  return !0;
}
function Ni(e, t, n, r, l, o) {
  if (zt = o, H = t, t.memoizedState = null, t.updateQueue = null, t.lanes = 0, zr.current = e === null || e.memoizedState === null ? Ed : Cd, e = n(r, l), Rn) {
    o = 0;
    do {
      if (Rn = !1, Gn = 0, 25 <= o) throw Error(k(301));
      o += 1, b = Z = null, t.updateQueue = null, zr.current = _d, e = n(r, l);
    } while (Rn);
  }
  if (zr.current = tl, t = Z !== null && Z.next !== null, zt = 0, b = Z = H = null, el = !1, t) throw Error(k(300));
  return e;
}
function Pi() {
  var e = Gn !== 0;
  return Gn = 0, e;
}
function Ve() {
  var e = { memoizedState: null, baseState: null, baseQueue: null, queue: null, next: null };
  return b === null ? H.memoizedState = b = e : b = b.next = e, b;
}
function Te() {
  if (Z === null) {
    var e = H.alternate;
    e = e !== null ? e.memoizedState : null;
  } else e = Z.next;
  var t = b === null ? H.memoizedState : b.next;
  if (t !== null) b = t, Z = e;
  else {
    if (e === null) throw Error(k(310));
    Z = e, e = { memoizedState: Z.memoizedState, baseState: Z.baseState, baseQueue: Z.baseQueue, queue: Z.queue, next: null }, b === null ? H.memoizedState = b = e : b = b.next = e;
  }
  return b;
}
function Zn(e, t) {
  return typeof t == "function" ? t(e) : t;
}
function Wl(e) {
  var t = Te(), n = t.queue;
  if (n === null) throw Error(k(311));
  n.lastRenderedReducer = e;
  var r = Z, l = r.baseQueue, o = n.pending;
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
      var m = c.lane;
      if ((zt & m) === m) s !== null && (s = s.next = { lane: 0, action: c.action, hasEagerState: c.hasEagerState, eagerState: c.eagerState, next: null }), r = c.hasEagerState ? c.eagerState : e(r, c.action);
      else {
        var h = {
          lane: m,
          action: c.action,
          hasEagerState: c.hasEagerState,
          eagerState: c.eagerState,
          next: null
        };
        s === null ? (u = s = h, i = r) : s = s.next = h, H.lanes |= m, Rt |= m;
      }
      c = c.next;
    } while (c !== null && c !== o);
    s === null ? i = r : s.next = u, Ue(r, t.memoizedState) || (pe = !0), t.memoizedState = r, t.baseState = i, t.baseQueue = s, n.lastRenderedState = r;
  }
  if (e = n.interleaved, e !== null) {
    l = e;
    do
      o = l.lane, H.lanes |= o, Rt |= o, l = l.next;
    while (l !== e);
  } else l === null && (n.lanes = 0);
  return [t.memoizedState, n.dispatch];
}
function Kl(e) {
  var t = Te(), n = t.queue;
  if (n === null) throw Error(k(311));
  n.lastRenderedReducer = e;
  var r = n.dispatch, l = n.pending, o = t.memoizedState;
  if (l !== null) {
    n.pending = null;
    var i = l = l.next;
    do
      o = e(o, i.action), i = i.next;
    while (i !== l);
    Ue(o, t.memoizedState) || (pe = !0), t.memoizedState = o, t.baseQueue === null && (t.baseState = o), n.lastRenderedState = o;
  }
  return [o, r];
}
function Ca() {
}
function _a(e, t) {
  var n = H, r = Te(), l = t(), o = !Ue(r.memoizedState, l);
  if (o && (r.memoizedState = l, pe = !0), r = r.queue, ji(ja.bind(null, n, r, e), [e]), r.getSnapshot !== t || o || b !== null && b.memoizedState.tag & 1) {
    if (n.flags |= 2048, Jn(9, Pa.bind(null, n, r, l, t), void 0, null), ee === null) throw Error(k(349));
    zt & 30 || Na(n, t, l);
  }
  return l;
}
function Na(e, t, n) {
  e.flags |= 16384, e = { getSnapshot: t, value: n }, t = H.updateQueue, t === null ? (t = { lastEffect: null, stores: null }, H.updateQueue = t, t.stores = [e]) : (n = t.stores, n === null ? t.stores = [e] : n.push(e));
}
function Pa(e, t, n, r) {
  t.value = n, t.getSnapshot = r, La(t) && Ta(e);
}
function ja(e, t, n) {
  return n(function() {
    La(t) && Ta(e);
  });
}
function La(e) {
  var t = e.getSnapshot;
  e = e.value;
  try {
    var n = t();
    return !Ue(e, n);
  } catch {
    return !0;
  }
}
function Ta(e) {
  var t = Je(e, 1);
  t !== null && Fe(t, e, 1, -1);
}
function Tu(e) {
  var t = Ve();
  return typeof e == "function" && (e = e()), t.memoizedState = t.baseState = e, e = { pending: null, interleaved: null, lanes: 0, dispatch: null, lastRenderedReducer: Zn, lastRenderedState: e }, t.queue = e, e = e.dispatch = xd.bind(null, H, e), [t.memoizedState, e];
}
function Jn(e, t, n, r) {
  return e = { tag: e, create: t, destroy: n, deps: r, next: null }, t = H.updateQueue, t === null ? (t = { lastEffect: null, stores: null }, H.updateQueue = t, t.lastEffect = e.next = e) : (n = t.lastEffect, n === null ? t.lastEffect = e.next = e : (r = n.next, n.next = e, e.next = r, t.lastEffect = e)), e;
}
function za() {
  return Te().memoizedState;
}
function Rr(e, t, n, r) {
  var l = Ve();
  H.flags |= e, l.memoizedState = Jn(1 | t, n, void 0, r === void 0 ? null : r);
}
function ml(e, t, n, r) {
  var l = Te();
  r = r === void 0 ? null : r;
  var o = void 0;
  if (Z !== null) {
    var i = Z.memoizedState;
    if (o = i.destroy, r !== null && _i(r, i.deps)) {
      l.memoizedState = Jn(t, n, o, r);
      return;
    }
  }
  H.flags |= e, l.memoizedState = Jn(1 | t, n, o, r);
}
function zu(e, t) {
  return Rr(8390656, 8, e, t);
}
function ji(e, t) {
  return ml(2048, 8, e, t);
}
function Ra(e, t) {
  return ml(4, 2, e, t);
}
function Ma(e, t) {
  return ml(4, 4, e, t);
}
function Ia(e, t) {
  if (typeof t == "function") return e = e(), t(e), function() {
    t(null);
  };
  if (t != null) return e = e(), t.current = e, function() {
    t.current = null;
  };
}
function Da(e, t, n) {
  return n = n != null ? n.concat([e]) : null, ml(4, 4, Ia.bind(null, t, e), n);
}
function Li() {
}
function Oa(e, t) {
  var n = Te();
  t = t === void 0 ? null : t;
  var r = n.memoizedState;
  return r !== null && t !== null && _i(t, r[1]) ? r[0] : (n.memoizedState = [e, t], e);
}
function Fa(e, t) {
  var n = Te();
  t = t === void 0 ? null : t;
  var r = n.memoizedState;
  return r !== null && t !== null && _i(t, r[1]) ? r[0] : (e = e(), n.memoizedState = [e, t], e);
}
function Ua(e, t, n) {
  return zt & 21 ? (Ue(n, t) || (n = Hs(), H.lanes |= n, Rt |= n, e.baseState = !0), t) : (e.baseState && (e.baseState = !1, pe = !0), e.memoizedState = n);
}
function kd(e, t) {
  var n = O;
  O = n !== 0 && 4 > n ? n : 4, e(!0);
  var r = Ql.transition;
  Ql.transition = {};
  try {
    e(!1), t();
  } finally {
    O = n, Ql.transition = r;
  }
}
function Aa() {
  return Te().memoizedState;
}
function Sd(e, t, n) {
  var r = pt(e);
  if (n = { lane: r, action: n, hasEagerState: !1, eagerState: null, next: null }, Va(e)) $a(t, n);
  else if (n = Sa(e, t, n, r), n !== null) {
    var l = ae();
    Fe(n, e, r, l), Ba(n, t, r);
  }
}
function xd(e, t, n) {
  var r = pt(e), l = { lane: r, action: n, hasEagerState: !1, eagerState: null, next: null };
  if (Va(e)) $a(t, l);
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
    n = Sa(e, t, l, r), n !== null && (l = ae(), Fe(n, e, r, l), Ba(n, t, r));
  }
}
function Va(e) {
  var t = e.alternate;
  return e === H || t !== null && t === H;
}
function $a(e, t) {
  Rn = el = !0;
  var n = e.pending;
  n === null ? t.next = t : (t.next = n.next, n.next = t), e.pending = t;
}
function Ba(e, t, n) {
  if (n & 4194240) {
    var r = t.lanes;
    r &= e.pendingLanes, n |= r, t.lanes = n, ii(e, n);
  }
}
var tl = { readContext: Le, useCallback: le, useContext: le, useEffect: le, useImperativeHandle: le, useInsertionEffect: le, useLayoutEffect: le, useMemo: le, useReducer: le, useRef: le, useState: le, useDebugValue: le, useDeferredValue: le, useTransition: le, useMutableSource: le, useSyncExternalStore: le, useId: le, unstable_isNewReconciler: !1 }, Ed = { readContext: Le, useCallback: function(e, t) {
  return Ve().memoizedState = [e, t === void 0 ? null : t], e;
}, useContext: Le, useEffect: zu, useImperativeHandle: function(e, t, n) {
  return n = n != null ? n.concat([e]) : null, Rr(
    4194308,
    4,
    Ia.bind(null, t, e),
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
  return t = n !== void 0 ? n(t) : t, r.memoizedState = r.baseState = t, e = { pending: null, interleaved: null, lanes: 0, dispatch: null, lastRenderedReducer: e, lastRenderedState: t }, r.queue = e, e = e.dispatch = Sd.bind(null, H, e), [r.memoizedState, e];
}, useRef: function(e) {
  var t = Ve();
  return e = { current: e }, t.memoizedState = e;
}, useState: Tu, useDebugValue: Li, useDeferredValue: function(e) {
  return Ve().memoizedState = e;
}, useTransition: function() {
  var e = Tu(!1), t = e[0];
  return e = kd.bind(null, e[1]), Ve().memoizedState = e, [t, e];
}, useMutableSource: function() {
}, useSyncExternalStore: function(e, t, n) {
  var r = H, l = Ve();
  if (V) {
    if (n === void 0) throw Error(k(407));
    n = n();
  } else {
    if (n = t(), ee === null) throw Error(k(349));
    zt & 30 || Na(r, t, n);
  }
  l.memoizedState = n;
  var o = { value: n, getSnapshot: t };
  return l.queue = o, zu(ja.bind(
    null,
    r,
    o,
    e
  ), [e]), r.flags |= 2048, Jn(9, Pa.bind(null, r, o, n, t), void 0, null), n;
}, useId: function() {
  var e = Ve(), t = ee.identifierPrefix;
  if (V) {
    var n = Ye, r = Ke;
    n = (r & ~(1 << 32 - Oe(r) - 1)).toString(32) + n, t = ":" + t + "R" + n, n = Gn++, 0 < n && (t += "H" + n.toString(32)), t += ":";
  } else n = wd++, t = ":" + t + "r" + n.toString(32) + ":";
  return e.memoizedState = t;
}, unstable_isNewReconciler: !1 }, Cd = {
  readContext: Le,
  useCallback: Oa,
  useContext: Le,
  useEffect: ji,
  useImperativeHandle: Da,
  useInsertionEffect: Ra,
  useLayoutEffect: Ma,
  useMemo: Fa,
  useReducer: Wl,
  useRef: za,
  useState: function() {
    return Wl(Zn);
  },
  useDebugValue: Li,
  useDeferredValue: function(e) {
    var t = Te();
    return Ua(t, Z.memoizedState, e);
  },
  useTransition: function() {
    var e = Wl(Zn)[0], t = Te().memoizedState;
    return [e, t];
  },
  useMutableSource: Ca,
  useSyncExternalStore: _a,
  useId: Aa,
  unstable_isNewReconciler: !1
}, _d = { readContext: Le, useCallback: Oa, useContext: Le, useEffect: ji, useImperativeHandle: Da, useInsertionEffect: Ra, useLayoutEffect: Ma, useMemo: Fa, useReducer: Kl, useRef: za, useState: function() {
  return Kl(Zn);
}, useDebugValue: Li, useDeferredValue: function(e) {
  var t = Te();
  return Z === null ? t.memoizedState = e : Ua(t, Z.memoizedState, e);
}, useTransition: function() {
  var e = Kl(Zn)[0], t = Te().memoizedState;
  return [e, t];
}, useMutableSource: Ca, useSyncExternalStore: _a, useId: Aa, unstable_isNewReconciler: !1 };
function Me(e, t) {
  if (e && e.defaultProps) {
    t = Q({}, t), e = e.defaultProps;
    for (var n in e) t[n] === void 0 && (t[n] = e[n]);
    return t;
  }
  return t;
}
function Ro(e, t, n, r) {
  t = e.memoizedState, n = n(r, t), n = n == null ? t : Q({}, t, n), e.memoizedState = n, e.lanes === 0 && (e.updateQueue.baseState = n);
}
var hl = { isMounted: function(e) {
  return (e = e._reactInternals) ? Dt(e) === e : !1;
}, enqueueSetState: function(e, t, n) {
  e = e._reactInternals;
  var r = ae(), l = pt(e), o = Xe(r, l);
  o.payload = t, n != null && (o.callback = n), t = ft(e, o, l), t !== null && (Fe(t, e, l, r), Tr(t, e, l));
}, enqueueReplaceState: function(e, t, n) {
  e = e._reactInternals;
  var r = ae(), l = pt(e), o = Xe(r, l);
  o.tag = 1, o.payload = t, n != null && (o.callback = n), t = ft(e, o, l), t !== null && (Fe(t, e, l, r), Tr(t, e, l));
}, enqueueForceUpdate: function(e, t) {
  e = e._reactInternals;
  var n = ae(), r = pt(e), l = Xe(n, r);
  l.tag = 2, t != null && (l.callback = t), t = ft(e, l, r), t !== null && (Fe(t, e, r, n), Tr(t, e, r));
} };
function Ru(e, t, n, r, l, o, i) {
  return e = e.stateNode, typeof e.shouldComponentUpdate == "function" ? e.shouldComponentUpdate(r, o, i) : t.prototype && t.prototype.isPureReactComponent ? !Hn(n, r) || !Hn(l, o) : !0;
}
function Ha(e, t, n) {
  var r = !1, l = vt, o = t.contextType;
  return typeof o == "object" && o !== null ? o = Le(o) : (l = he(t) ? Lt : ue.current, r = t.contextTypes, o = (r = r != null) ? nn(e, l) : vt), t = new t(n, o), e.memoizedState = t.state !== null && t.state !== void 0 ? t.state : null, t.updater = hl, e.stateNode = t, t._reactInternals = e, r && (e = e.stateNode, e.__reactInternalMemoizedUnmaskedChildContext = l, e.__reactInternalMemoizedMaskedChildContext = o), t;
}
function Mu(e, t, n, r) {
  e = t.state, typeof t.componentWillReceiveProps == "function" && t.componentWillReceiveProps(n, r), typeof t.UNSAFE_componentWillReceiveProps == "function" && t.UNSAFE_componentWillReceiveProps(n, r), t.state !== e && hl.enqueueReplaceState(t, t.state, null);
}
function Mo(e, t, n, r) {
  var l = e.stateNode;
  l.props = n, l.state = e.memoizedState, l.refs = {}, Si(e);
  var o = t.contextType;
  typeof o == "object" && o !== null ? l.context = Le(o) : (o = he(t) ? Lt : ue.current, l.context = nn(e, o)), l.state = e.memoizedState, o = t.getDerivedStateFromProps, typeof o == "function" && (Ro(e, t, o, n), l.state = e.memoizedState), typeof t.getDerivedStateFromProps == "function" || typeof l.getSnapshotBeforeUpdate == "function" || typeof l.UNSAFE_componentWillMount != "function" && typeof l.componentWillMount != "function" || (t = l.state, typeof l.componentWillMount == "function" && l.componentWillMount(), typeof l.UNSAFE_componentWillMount == "function" && l.UNSAFE_componentWillMount(), t !== l.state && hl.enqueueReplaceState(l, l.state, null), qr(e, n, l, r), l.state = e.memoizedState), typeof l.componentDidMount == "function" && (e.flags |= 4194308);
}
function un(e, t) {
  try {
    var n = "", r = t;
    do
      n += qc(r), r = r.return;
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
var Nd = typeof WeakMap == "function" ? WeakMap : Map;
function Qa(e, t, n) {
  n = Xe(-1, n), n.tag = 3, n.payload = { element: null };
  var r = t.value;
  return n.callback = function() {
    rl || (rl = !0, Qo = r), Io(e, t);
  }, n;
}
function Wa(e, t, n) {
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
    r = e.pingCache = new Nd();
    var l = /* @__PURE__ */ new Set();
    r.set(t, l);
  } else l = r.get(t), l === void 0 && (l = /* @__PURE__ */ new Set(), r.set(t, l));
  l.has(n) || (l.add(n), e = Vd.bind(null, e, t, n), t.then(e, e));
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
var Pd = be.ReactCurrentOwner, pe = !1;
function se(e, t, n, r) {
  t.child = e === null ? ka(t, null, n, r) : ln(t, e.child, n, r);
}
function Fu(e, t, n, r, l) {
  n = n.render;
  var o = t.ref;
  return bt(t, l), r = Ni(e, t, n, r, o, l), n = Pi(), e !== null && !pe ? (t.updateQueue = e.updateQueue, t.flags &= -2053, e.lanes &= ~l, qe(e, t, l)) : (V && n && mi(t), t.flags |= 1, se(e, t, r, l), t.child);
}
function Uu(e, t, n, r, l) {
  if (e === null) {
    var o = n.type;
    return typeof o == "function" && !Fi(o) && o.defaultProps === void 0 && n.compare === null && n.defaultProps === void 0 ? (t.tag = 15, t.type = o, Ka(e, t, o, r, l)) : (e = Or(n.type, null, r, t, t.mode, l), e.ref = t.ref, e.return = t, t.child = e);
  }
  if (o = e.child, !(e.lanes & l)) {
    var i = o.memoizedProps;
    if (n = n.compare, n = n !== null ? n : Hn, n(i, r) && e.ref === t.ref) return qe(e, t, l);
  }
  return t.flags |= 1, e = mt(o, r), e.ref = t.ref, e.return = t, t.child = e;
}
function Ka(e, t, n, r, l) {
  if (e !== null) {
    var o = e.memoizedProps;
    if (Hn(o, r) && e.ref === t.ref) if (pe = !1, t.pendingProps = r = o, (e.lanes & l) !== 0) e.flags & 131072 && (pe = !0);
    else return t.lanes = e.lanes, qe(e, t, l);
  }
  return Do(e, t, n, r, l);
}
function Ya(e, t, n) {
  var r = t.pendingProps, l = r.children, o = e !== null ? e.memoizedState : null;
  if (r.mode === "hidden") if (!(t.mode & 1)) t.memoizedState = { baseLanes: 0, cachePool: null, transitions: null }, F(Xt, ge), ge |= n;
  else {
    if (!(n & 1073741824)) return e = o !== null ? o.baseLanes | n : n, t.lanes = t.childLanes = 1073741824, t.memoizedState = { baseLanes: e, cachePool: null, transitions: null }, t.updateQueue = null, F(Xt, ge), ge |= e, null;
    t.memoizedState = { baseLanes: 0, cachePool: null, transitions: null }, r = o !== null ? o.baseLanes : n, F(Xt, ge), ge |= r;
  }
  else o !== null ? (r = o.baseLanes | n, t.memoizedState = null) : r = n, F(Xt, ge), ge |= r;
  return se(e, t, l, n), t.child;
}
function Xa(e, t) {
  var n = t.ref;
  (e === null && n !== null || e !== null && e.ref !== n) && (t.flags |= 512, t.flags |= 2097152);
}
function Do(e, t, n, r, l) {
  var o = he(n) ? Lt : ue.current;
  return o = nn(t, o), bt(t, l), n = Ni(e, t, n, r, o, l), r = Pi(), e !== null && !pe ? (t.updateQueue = e.updateQueue, t.flags &= -2053, e.lanes &= ~l, qe(e, t, l)) : (V && r && mi(t), t.flags |= 1, se(e, t, n, l), t.child);
}
function Au(e, t, n, r, l) {
  if (he(n)) {
    var o = !0;
    Yr(t);
  } else o = !1;
  if (bt(t, l), t.stateNode === null) Mr(e, t), Ha(t, n, r), Mo(t, n, r, l), r = !0;
  else if (e === null) {
    var i = t.stateNode, u = t.memoizedProps;
    i.props = u;
    var s = i.context, c = n.contextType;
    typeof c == "object" && c !== null ? c = Le(c) : (c = he(n) ? Lt : ue.current, c = nn(t, c));
    var m = n.getDerivedStateFromProps, h = typeof m == "function" || typeof i.getSnapshotBeforeUpdate == "function";
    h || typeof i.UNSAFE_componentWillReceiveProps != "function" && typeof i.componentWillReceiveProps != "function" || (u !== r || s !== c) && Mu(t, i, r, c), nt = !1;
    var d = t.memoizedState;
    i.state = d, qr(t, r, i, l), s = t.memoizedState, u !== r || d !== s || me.current || nt ? (typeof m == "function" && (Ro(t, n, m, r), s = t.memoizedState), (u = nt || Ru(t, n, u, r, d, s, c)) ? (h || typeof i.UNSAFE_componentWillMount != "function" && typeof i.componentWillMount != "function" || (typeof i.componentWillMount == "function" && i.componentWillMount(), typeof i.UNSAFE_componentWillMount == "function" && i.UNSAFE_componentWillMount()), typeof i.componentDidMount == "function" && (t.flags |= 4194308)) : (typeof i.componentDidMount == "function" && (t.flags |= 4194308), t.memoizedProps = r, t.memoizedState = s), i.props = r, i.state = s, i.context = c, r = u) : (typeof i.componentDidMount == "function" && (t.flags |= 4194308), r = !1);
  } else {
    i = t.stateNode, xa(e, t), u = t.memoizedProps, c = t.type === t.elementType ? u : Me(t.type, u), i.props = c, h = t.pendingProps, d = i.context, s = n.contextType, typeof s == "object" && s !== null ? s = Le(s) : (s = he(n) ? Lt : ue.current, s = nn(t, s));
    var w = n.getDerivedStateFromProps;
    (m = typeof w == "function" || typeof i.getSnapshotBeforeUpdate == "function") || typeof i.UNSAFE_componentWillReceiveProps != "function" && typeof i.componentWillReceiveProps != "function" || (u !== h || d !== s) && Mu(t, i, r, s), nt = !1, d = t.memoizedState, i.state = d, qr(t, r, i, l);
    var C = t.memoizedState;
    u !== h || d !== C || me.current || nt ? (typeof w == "function" && (Ro(t, n, w, r), C = t.memoizedState), (c = nt || Ru(t, n, c, r, d, C, s) || !1) ? (m || typeof i.UNSAFE_componentWillUpdate != "function" && typeof i.componentWillUpdate != "function" || (typeof i.componentWillUpdate == "function" && i.componentWillUpdate(r, C, s), typeof i.UNSAFE_componentWillUpdate == "function" && i.UNSAFE_componentWillUpdate(r, C, s)), typeof i.componentDidUpdate == "function" && (t.flags |= 4), typeof i.getSnapshotBeforeUpdate == "function" && (t.flags |= 1024)) : (typeof i.componentDidUpdate != "function" || u === e.memoizedProps && d === e.memoizedState || (t.flags |= 4), typeof i.getSnapshotBeforeUpdate != "function" || u === e.memoizedProps && d === e.memoizedState || (t.flags |= 1024), t.memoizedProps = r, t.memoizedState = C), i.props = r, i.state = C, i.context = s, r = c) : (typeof i.componentDidUpdate != "function" || u === e.memoizedProps && d === e.memoizedState || (t.flags |= 4), typeof i.getSnapshotBeforeUpdate != "function" || u === e.memoizedProps && d === e.memoizedState || (t.flags |= 1024), r = !1);
  }
  return Oo(e, t, n, r, o, l);
}
function Oo(e, t, n, r, l, o) {
  Xa(e, t);
  var i = (t.flags & 128) !== 0;
  if (!r && !i) return l && Cu(t, n, !1), qe(e, t, o);
  r = t.stateNode, Pd.current = t;
  var u = i && typeof n.getDerivedStateFromError != "function" ? null : r.render();
  return t.flags |= 1, e !== null && i ? (t.child = ln(t, e.child, null, o), t.child = ln(t, null, u, o)) : se(e, t, u, o), t.memoizedState = r.state, l && Cu(t, n, !0), t.child;
}
function Ga(e) {
  var t = e.stateNode;
  t.pendingContext ? Eu(e, t.pendingContext, t.pendingContext !== t.context) : t.context && Eu(e, t.context, !1), xi(e, t.containerInfo);
}
function Vu(e, t, n, r, l) {
  return rn(), vi(l), t.flags |= 256, se(e, t, n, r), t.child;
}
var Fo = { dehydrated: null, treeContext: null, retryLane: 0 };
function Uo(e) {
  return { baseLanes: e, cachePool: null, transitions: null };
}
function Za(e, t, n) {
  var r = t.pendingProps, l = B.current, o = !1, i = (t.flags & 128) !== 0, u;
  if ((u = i) || (u = e !== null && e.memoizedState === null ? !1 : (l & 2) !== 0), u ? (o = !0, t.flags &= -129) : (e === null || e.memoizedState !== null) && (l |= 1), F(B, l & 1), e === null)
    return To(t), e = t.memoizedState, e !== null && (e = e.dehydrated, e !== null) ? (t.mode & 1 ? e.data === "$!" ? t.lanes = 8 : t.lanes = 1073741824 : t.lanes = 1, null) : (i = r.children, e = r.fallback, o ? (r = t.mode, o = t.child, i = { mode: "hidden", children: i }, !(r & 1) && o !== null ? (o.childLanes = 0, o.pendingProps = i) : o = yl(i, r, 0, null), e = jt(e, r, n, null), o.return = t, e.return = t, o.sibling = e, t.child = o, t.child.memoizedState = Uo(n), t.memoizedState = Fo, e) : Ti(t, i));
  if (l = e.memoizedState, l !== null && (u = l.dehydrated, u !== null)) return jd(e, t, i, r, u, l, n);
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
function jd(e, t, n, r, l, o, i) {
  if (n)
    return t.flags & 256 ? (t.flags &= -257, r = Yl(Error(k(422))), Sr(e, t, i, r)) : t.memoizedState !== null ? (t.child = e.child, t.flags |= 128, null) : (o = r.fallback, l = t.mode, r = yl({ mode: "visible", children: r.children }, l, 0, null), o = jt(o, l, i, null), o.flags |= 2, r.return = t, o.return = t, r.sibling = o, t.child = r, t.mode & 1 && ln(t, e.child, null, i), t.child.memoizedState = Uo(i), t.memoizedState = Fo, o);
  if (!(t.mode & 1)) return Sr(e, t, i, null);
  if (l.data === "$!") {
    if (r = l.nextSibling && l.nextSibling.dataset, r) var u = r.dgst;
    return r = u, o = Error(k(419)), r = Yl(o, r, void 0), Sr(e, t, i, r);
  }
  if (u = (i & e.childLanes) !== 0, pe || u) {
    if (r = ee, r !== null) {
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
    return Oi(), r = Yl(Error(k(421))), Sr(e, t, i, r);
  }
  return l.data === "$?" ? (t.flags |= 128, t.child = e.child, t = $d.bind(null, e), l._reactRetry = t, null) : (e = o.treeContext, ye = ct(l.nextSibling), we = t, V = !0, De = null, e !== null && (_e[Ne++] = Ke, _e[Ne++] = Ye, _e[Ne++] = Tt, Ke = e.id, Ye = e.overflow, Tt = t), t = Ti(t, r.children), t.flags |= 4096, t);
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
function Ja(e, t, n) {
  var r = t.pendingProps, l = r.revealOrder, o = r.tail;
  if (se(e, t, r.children, n), r = B.current, r & 2) r = r & 1 | 2, t.flags |= 128;
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
  if (F(B, r), !(t.mode & 1)) t.memoizedState = null;
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
  if (e !== null && t.child !== e.child) throw Error(k(153));
  if (t.child !== null) {
    for (e = t.child, n = mt(e, e.pendingProps), t.child = n, n.return = t; e.sibling !== null; ) e = e.sibling, n = n.sibling = mt(e, e.pendingProps), n.return = t;
    n.sibling = null;
  }
  return t.child;
}
function Ld(e, t, n) {
  switch (t.tag) {
    case 3:
      Ga(t), rn();
      break;
    case 5:
      Ea(t);
      break;
    case 1:
      he(t.type) && Yr(t);
      break;
    case 4:
      xi(t, t.stateNode.containerInfo);
      break;
    case 10:
      var r = t.type._context, l = t.memoizedProps.value;
      F(Zr, r._currentValue), r._currentValue = l;
      break;
    case 13:
      if (r = t.memoizedState, r !== null)
        return r.dehydrated !== null ? (F(B, B.current & 1), t.flags |= 128, null) : n & t.child.childLanes ? Za(e, t, n) : (F(B, B.current & 1), e = qe(e, t, n), e !== null ? e.sibling : null);
      F(B, B.current & 1);
      break;
    case 19:
      if (r = (n & t.childLanes) !== 0, e.flags & 128) {
        if (r) return Ja(e, t, n);
        t.flags |= 128;
      }
      if (l = t.memoizedState, l !== null && (l.rendering = null, l.tail = null, l.lastEffect = null), F(B, B.current), r) break;
      return null;
    case 22:
    case 23:
      return t.lanes = 0, Ya(e, t, n);
  }
  return qe(e, t, n);
}
var qa, Ao, ba, ec;
qa = function(e, t) {
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
ba = function(e, t, n, r) {
  var l = e.memoizedProps;
  if (l !== r) {
    e = t.stateNode, Nt(He.current);
    var o = null;
    switch (n) {
      case "input":
        l = io(e, l), r = io(e, r), o = [];
        break;
      case "select":
        l = Q({}, l, { value: void 0 }), r = Q({}, r, { value: void 0 }), o = [];
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
      else c === "dangerouslySetInnerHTML" ? (s = s ? s.__html : void 0, u = u ? u.__html : void 0, s != null && u !== s && (o = o || []).push(c, s)) : c === "children" ? typeof s != "string" && typeof s != "number" || (o = o || []).push(c, "" + s) : c !== "suppressContentEditableWarning" && c !== "suppressHydrationWarning" && (On.hasOwnProperty(c) ? (s != null && c === "onScroll" && U("scroll", e), o || u === s || (o = [])) : (o = o || []).push(c, s));
    }
    n && (o = o || []).push("style", n);
    var c = o;
    (t.updateQueue = c) && (t.flags |= 4);
  }
};
ec = function(e, t, n, r) {
  n !== r && (t.flags |= 4);
};
function Sn(e, t) {
  if (!V) switch (e.tailMode) {
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
function Td(e, t, n) {
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
      return oe(t), null;
    case 1:
      return he(t.type) && Kr(), oe(t), null;
    case 3:
      return r = t.stateNode, on(), A(me), A(ue), Ci(), r.pendingContext && (r.context = r.pendingContext, r.pendingContext = null), (e === null || e.child === null) && (wr(t) ? t.flags |= 4 : e === null || e.memoizedState.isDehydrated && !(t.flags & 256) || (t.flags |= 1024, De !== null && (Yo(De), De = null))), Ao(e, t), oe(t), null;
    case 5:
      Ei(t);
      var l = Nt(Xn.current);
      if (n = t.type, e !== null && t.stateNode != null) ba(e, t, n, r, l), e.ref !== t.ref && (t.flags |= 512, t.flags |= 2097152);
      else {
        if (!r) {
          if (t.stateNode === null) throw Error(k(166));
          return oe(t), null;
        }
        if (e = Nt(He.current), wr(t)) {
          r = t.stateNode, n = t.type;
          var o = t.memoizedProps;
          switch (r[$e] = t, r[Kn] = o, e = (t.mode & 1) !== 0, n) {
            case "dialog":
              U("cancel", r), U("close", r);
              break;
            case "iframe":
            case "object":
            case "embed":
              U("load", r);
              break;
            case "video":
            case "audio":
              for (l = 0; l < Nn.length; l++) U(Nn[l], r);
              break;
            case "source":
              U("error", r);
              break;
            case "img":
            case "image":
            case "link":
              U(
                "error",
                r
              ), U("load", r);
              break;
            case "details":
              U("toggle", r);
              break;
            case "input":
              Zi(r, o), U("invalid", r);
              break;
            case "select":
              r._wrapperState = { wasMultiple: !!o.multiple }, U("invalid", r);
              break;
            case "textarea":
              qi(r, o), U("invalid", r);
          }
          fo(n, o), l = null;
          for (var i in o) if (o.hasOwnProperty(i)) {
            var u = o[i];
            i === "children" ? typeof u == "string" ? r.textContent !== u && (o.suppressHydrationWarning !== !0 && yr(r.textContent, u, e), l = ["children", u]) : typeof u == "number" && r.textContent !== "" + u && (o.suppressHydrationWarning !== !0 && yr(
              r.textContent,
              u,
              e
            ), l = ["children", "" + u]) : On.hasOwnProperty(i) && u != null && i === "onScroll" && U("scroll", r);
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
          i = l.nodeType === 9 ? l : l.ownerDocument, e === "http://www.w3.org/1999/xhtml" && (e = js(n)), e === "http://www.w3.org/1999/xhtml" ? n === "script" ? (e = i.createElement("div"), e.innerHTML = "<script><\/script>", e = e.removeChild(e.firstChild)) : typeof r.is == "string" ? e = i.createElement(n, { is: r.is }) : (e = i.createElement(n), n === "select" && (i = e, r.multiple ? i.multiple = !0 : r.size && (i.size = r.size))) : e = i.createElementNS(e, n), e[$e] = t, e[Kn] = r, qa(e, t, !1, !1), t.stateNode = e;
          e: {
            switch (i = po(n, r), n) {
              case "dialog":
                U("cancel", e), U("close", e), l = r;
                break;
              case "iframe":
              case "object":
              case "embed":
                U("load", e), l = r;
                break;
              case "video":
              case "audio":
                for (l = 0; l < Nn.length; l++) U(Nn[l], e);
                l = r;
                break;
              case "source":
                U("error", e), l = r;
                break;
              case "img":
              case "image":
              case "link":
                U(
                  "error",
                  e
                ), U("load", e), l = r;
                break;
              case "details":
                U("toggle", e), l = r;
                break;
              case "input":
                Zi(e, r), l = io(e, r), U("invalid", e);
                break;
              case "option":
                l = r;
                break;
              case "select":
                e._wrapperState = { wasMultiple: !!r.multiple }, l = Q({}, r, { value: void 0 }), U("invalid", e);
                break;
              case "textarea":
                qi(e, r), l = ao(e, r), U("invalid", e);
                break;
              default:
                l = r;
            }
            fo(n, l), u = l;
            for (o in u) if (u.hasOwnProperty(o)) {
              var s = u[o];
              o === "style" ? zs(e, s) : o === "dangerouslySetInnerHTML" ? (s = s ? s.__html : void 0, s != null && Ls(e, s)) : o === "children" ? typeof s == "string" ? (n !== "textarea" || s !== "") && Fn(e, s) : typeof s == "number" && Fn(e, "" + s) : o !== "suppressContentEditableWarning" && o !== "suppressHydrationWarning" && o !== "autoFocus" && (On.hasOwnProperty(o) ? s != null && o === "onScroll" && U("scroll", e) : s != null && ei(e, o, s, i));
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
      return oe(t), null;
    case 6:
      if (e && t.stateNode != null) ec(e, t, e.memoizedProps, r);
      else {
        if (typeof r != "string" && t.stateNode === null) throw Error(k(166));
        if (n = Nt(Xn.current), Nt(He.current), wr(t)) {
          if (r = t.stateNode, n = t.memoizedProps, r[$e] = t, (o = r.nodeValue !== n) && (e = we, e !== null)) switch (e.tag) {
            case 3:
              yr(r.nodeValue, n, (e.mode & 1) !== 0);
              break;
            case 5:
              e.memoizedProps.suppressHydrationWarning !== !0 && yr(r.nodeValue, n, (e.mode & 1) !== 0);
          }
          o && (t.flags |= 4);
        } else r = (n.nodeType === 9 ? n : n.ownerDocument).createTextNode(r), r[$e] = t, t.stateNode = r;
      }
      return oe(t), null;
    case 13:
      if (A(B), r = t.memoizedState, e === null || e.memoizedState !== null && e.memoizedState.dehydrated !== null) {
        if (V && ye !== null && t.mode & 1 && !(t.flags & 128)) ya(), rn(), t.flags |= 98560, o = !1;
        else if (o = wr(t), r !== null && r.dehydrated !== null) {
          if (e === null) {
            if (!o) throw Error(k(318));
            if (o = t.memoizedState, o = o !== null ? o.dehydrated : null, !o) throw Error(k(317));
            o[$e] = t;
          } else rn(), !(t.flags & 128) && (t.memoizedState = null), t.flags |= 4;
          oe(t), o = !1;
        } else De !== null && (Yo(De), De = null), o = !0;
        if (!o) return t.flags & 65536 ? t : null;
      }
      return t.flags & 128 ? (t.lanes = n, t) : (r = r !== null, r !== (e !== null && e.memoizedState !== null) && r && (t.child.flags |= 8192, t.mode & 1 && (e === null || B.current & 1 ? J === 0 && (J = 3) : Oi())), t.updateQueue !== null && (t.flags |= 4), oe(t), null);
    case 4:
      return on(), Ao(e, t), e === null && Qn(t.stateNode.containerInfo), oe(t), null;
    case 10:
      return wi(t.type._context), oe(t), null;
    case 17:
      return he(t.type) && Kr(), oe(t), null;
    case 19:
      if (A(B), o = t.memoizedState, o === null) return oe(t), null;
      if (r = (t.flags & 128) !== 0, i = o.rendering, i === null) if (r) Sn(o, !1);
      else {
        if (J !== 0 || e !== null && e.flags & 128) for (e = t.child; e !== null; ) {
          if (i = br(e), i !== null) {
            for (t.flags |= 128, Sn(o, !1), r = i.updateQueue, r !== null && (t.updateQueue = r, t.flags |= 4), t.subtreeFlags = 0, r = n, n = t.child; n !== null; ) o = n, e = r, o.flags &= 14680066, i = o.alternate, i === null ? (o.childLanes = 0, o.lanes = e, o.child = null, o.subtreeFlags = 0, o.memoizedProps = null, o.memoizedState = null, o.updateQueue = null, o.dependencies = null, o.stateNode = null) : (o.childLanes = i.childLanes, o.lanes = i.lanes, o.child = i.child, o.subtreeFlags = 0, o.deletions = null, o.memoizedProps = i.memoizedProps, o.memoizedState = i.memoizedState, o.updateQueue = i.updateQueue, o.type = i.type, e = i.dependencies, o.dependencies = e === null ? null : { lanes: e.lanes, firstContext: e.firstContext }), n = n.sibling;
            return F(B, B.current & 1 | 2), t.child;
          }
          e = e.sibling;
        }
        o.tail !== null && X() > sn && (t.flags |= 128, r = !0, Sn(o, !1), t.lanes = 4194304);
      }
      else {
        if (!r) if (e = br(i), e !== null) {
          if (t.flags |= 128, r = !0, n = e.updateQueue, n !== null && (t.updateQueue = n, t.flags |= 4), Sn(o, !0), o.tail === null && o.tailMode === "hidden" && !i.alternate && !V) return oe(t), null;
        } else 2 * X() - o.renderingStartTime > sn && n !== 1073741824 && (t.flags |= 128, r = !0, Sn(o, !1), t.lanes = 4194304);
        o.isBackwards ? (i.sibling = t.child, t.child = i) : (n = o.last, n !== null ? n.sibling = i : t.child = i, o.last = i);
      }
      return o.tail !== null ? (t = o.tail, o.rendering = t, o.tail = t.sibling, o.renderingStartTime = X(), t.sibling = null, n = B.current, F(B, r ? n & 1 | 2 : n & 1), t) : (oe(t), null);
    case 22:
    case 23:
      return Di(), r = t.memoizedState !== null, e !== null && e.memoizedState !== null !== r && (t.flags |= 8192), r && t.mode & 1 ? ge & 1073741824 && (oe(t), t.subtreeFlags & 6 && (t.flags |= 8192)) : oe(t), null;
    case 24:
      return null;
    case 25:
      return null;
  }
  throw Error(k(156, t.tag));
}
function zd(e, t) {
  switch (hi(t), t.tag) {
    case 1:
      return he(t.type) && Kr(), e = t.flags, e & 65536 ? (t.flags = e & -65537 | 128, t) : null;
    case 3:
      return on(), A(me), A(ue), Ci(), e = t.flags, e & 65536 && !(e & 128) ? (t.flags = e & -65537 | 128, t) : null;
    case 5:
      return Ei(t), null;
    case 13:
      if (A(B), e = t.memoizedState, e !== null && e.dehydrated !== null) {
        if (t.alternate === null) throw Error(k(340));
        rn();
      }
      return e = t.flags, e & 65536 ? (t.flags = e & -65537 | 128, t) : null;
    case 19:
      return A(B), null;
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
var xr = !1, ie = !1, Rd = typeof WeakSet == "function" ? WeakSet : Set, j = null;
function Yt(e, t) {
  var n = e.ref;
  if (n !== null) if (typeof n == "function") try {
    n(null);
  } catch (r) {
    K(e, t, r);
  }
  else n.current = null;
}
function Vo(e, t, n) {
  try {
    n();
  } catch (r) {
    K(e, t, r);
  }
}
var Bu = !1;
function Md(e, t) {
  if (Eo = Br, e = oa(), pi(e)) {
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
        var i = 0, u = -1, s = -1, c = 0, m = 0, h = e, d = null;
        t: for (; ; ) {
          for (var w; h !== n || l !== 0 && h.nodeType !== 3 || (u = i + l), h !== o || r !== 0 && h.nodeType !== 3 || (s = i + r), h.nodeType === 3 && (i += h.nodeValue.length), (w = h.firstChild) !== null; )
            d = h, h = w;
          for (; ; ) {
            if (h === e) break t;
            if (d === n && ++c === l && (u = i), d === o && ++m === r && (s = i), (w = h.nextSibling) !== null) break;
            h = d, d = h.parentNode;
          }
          h = w;
        }
        n = u === -1 || s === -1 ? null : { start: u, end: s };
      } else n = null;
    }
    n = n || { start: 0, end: 0 };
  } else n = null;
  for (Co = { focusedElem: e, selectionRange: n }, Br = !1, j = t; j !== null; ) if (t = j, e = t.child, (t.subtreeFlags & 1028) !== 0 && e !== null) e.return = t, j = e;
  else for (; j !== null; ) {
    t = j;
    try {
      var C = t.alternate;
      if (t.flags & 1024) switch (t.tag) {
        case 0:
        case 11:
        case 15:
          break;
        case 1:
          if (C !== null) {
            var S = C.memoizedProps, z = C.memoizedState, f = t.stateNode, a = f.getSnapshotBeforeUpdate(t.elementType === t.type ? S : Me(t.type, S), z);
            f.__reactInternalSnapshotBeforeUpdate = a;
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
          throw Error(k(163));
      }
    } catch (y) {
      K(t, t.return, y);
    }
    if (e = t.sibling, e !== null) {
      e.return = t.return, j = e;
      break;
    }
    j = t.return;
  }
  return C = Bu, Bu = !1, C;
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
function tc(e) {
  var t = e.alternate;
  t !== null && (e.alternate = null, tc(t)), e.child = null, e.deletions = null, e.sibling = null, e.tag === 5 && (t = e.stateNode, t !== null && (delete t[$e], delete t[Kn], delete t[Po], delete t[hd], delete t[vd])), e.stateNode = null, e.return = null, e.dependencies = null, e.memoizedProps = null, e.memoizedState = null, e.pendingProps = null, e.stateNode = null, e.updateQueue = null;
}
function nc(e) {
  return e.tag === 5 || e.tag === 3 || e.tag === 4;
}
function Hu(e) {
  e: for (; ; ) {
    for (; e.sibling === null; ) {
      if (e.return === null || nc(e.return)) return null;
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
var te = null, Ie = !1;
function et(e, t, n) {
  for (n = n.child; n !== null; ) rc(e, t, n), n = n.sibling;
}
function rc(e, t, n) {
  if (Be && typeof Be.onCommitFiberUnmount == "function") try {
    Be.onCommitFiberUnmount(sl, n);
  } catch {
  }
  switch (n.tag) {
    case 5:
      ie || Yt(n, t);
    case 6:
      var r = te, l = Ie;
      te = null, et(e, t, n), te = r, Ie = l, te !== null && (Ie ? (e = te, n = n.stateNode, e.nodeType === 8 ? e.parentNode.removeChild(n) : e.removeChild(n)) : te.removeChild(n.stateNode));
      break;
    case 18:
      te !== null && (Ie ? (e = te, n = n.stateNode, e.nodeType === 8 ? $l(e.parentNode, n) : e.nodeType === 1 && $l(e, n), $n(e)) : $l(te, n.stateNode));
      break;
    case 4:
      r = te, l = Ie, te = n.stateNode.containerInfo, Ie = !0, et(e, t, n), te = r, Ie = l;
      break;
    case 0:
    case 11:
    case 14:
    case 15:
      if (!ie && (r = n.updateQueue, r !== null && (r = r.lastEffect, r !== null))) {
        l = r = r.next;
        do {
          var o = l, i = o.destroy;
          o = o.tag, i !== void 0 && (o & 2 || o & 4) && Vo(n, t, i), l = l.next;
        } while (l !== r);
      }
      et(e, t, n);
      break;
    case 1:
      if (!ie && (Yt(n, t), r = n.stateNode, typeof r.componentWillUnmount == "function")) try {
        r.props = n.memoizedProps, r.state = n.memoizedState, r.componentWillUnmount();
      } catch (u) {
        K(n, t, u);
      }
      et(e, t, n);
      break;
    case 21:
      et(e, t, n);
      break;
    case 22:
      n.mode & 1 ? (ie = (r = ie) || n.memoizedState !== null, et(e, t, n), ie = r) : et(e, t, n);
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
    n === null && (n = e.stateNode = new Rd()), t.forEach(function(r) {
      var l = Bd.bind(null, e, r);
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
            te = u.stateNode, Ie = !1;
            break e;
          case 3:
            te = u.stateNode.containerInfo, Ie = !0;
            break e;
          case 4:
            te = u.stateNode.containerInfo, Ie = !0;
            break e;
        }
        u = u.return;
      }
      if (te === null) throw Error(k(160));
      rc(o, i, l), te = null, Ie = !1;
      var s = l.alternate;
      s !== null && (s.return = null), l.return = null;
    } catch (c) {
      K(l, t, c);
    }
  }
  if (t.subtreeFlags & 12854) for (t = t.child; t !== null; ) lc(t, e), t = t.sibling;
}
function lc(e, t) {
  var n = e.alternate, r = e.flags;
  switch (e.tag) {
    case 0:
    case 11:
    case 14:
    case 15:
      if (Re(t, e), Ae(e), r & 4) {
        try {
          Mn(3, e, e.return), vl(3, e);
        } catch (S) {
          K(e, e.return, S);
        }
        try {
          Mn(5, e, e.return);
        } catch (S) {
          K(e, e.return, S);
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
        } catch (S) {
          K(e, e.return, S);
        }
      }
      if (r & 4 && (l = e.stateNode, l != null)) {
        var o = e.memoizedProps, i = n !== null ? n.memoizedProps : o, u = e.type, s = e.updateQueue;
        if (e.updateQueue = null, s !== null) try {
          u === "input" && o.type === "radio" && o.name != null && Ns(l, o), po(u, i);
          var c = po(u, o);
          for (i = 0; i < s.length; i += 2) {
            var m = s[i], h = s[i + 1];
            m === "style" ? zs(l, h) : m === "dangerouslySetInnerHTML" ? Ls(l, h) : m === "children" ? Fn(l, h) : ei(l, m, h, c);
          }
          switch (u) {
            case "input":
              uo(l, o);
              break;
            case "textarea":
              Ps(l, o);
              break;
            case "select":
              var d = l._wrapperState.wasMultiple;
              l._wrapperState.wasMultiple = !!o.multiple;
              var w = o.value;
              w != null ? Gt(l, !!o.multiple, w, !1) : d !== !!o.multiple && (o.defaultValue != null ? Gt(
                l,
                !!o.multiple,
                o.defaultValue,
                !0
              ) : Gt(l, !!o.multiple, o.multiple ? [] : "", !1));
          }
          l[Kn] = o;
        } catch (S) {
          K(e, e.return, S);
        }
      }
      break;
    case 6:
      if (Re(t, e), Ae(e), r & 4) {
        if (e.stateNode === null) throw Error(k(162));
        l = e.stateNode, o = e.memoizedProps;
        try {
          l.nodeValue = o;
        } catch (S) {
          K(e, e.return, S);
        }
      }
      break;
    case 3:
      if (Re(t, e), Ae(e), r & 4 && n !== null && n.memoizedState.isDehydrated) try {
        $n(t.containerInfo);
      } catch (S) {
        K(e, e.return, S);
      }
      break;
    case 4:
      Re(t, e), Ae(e);
      break;
    case 13:
      Re(t, e), Ae(e), l = e.child, l.flags & 8192 && (o = l.memoizedState !== null, l.stateNode.isHidden = o, !o || l.alternate !== null && l.alternate.memoizedState !== null || (Mi = X())), r & 4 && Qu(e);
      break;
    case 22:
      if (m = n !== null && n.memoizedState !== null, e.mode & 1 ? (ie = (c = ie) || m, Re(t, e), ie = c) : Re(t, e), Ae(e), r & 8192) {
        if (c = e.memoizedState !== null, (e.stateNode.isHidden = c) && !m && e.mode & 1) for (j = e, m = e.child; m !== null; ) {
          for (h = j = m; j !== null; ) {
            switch (d = j, w = d.child, d.tag) {
              case 0:
              case 11:
              case 14:
              case 15:
                Mn(4, d, d.return);
                break;
              case 1:
                Yt(d, d.return);
                var C = d.stateNode;
                if (typeof C.componentWillUnmount == "function") {
                  r = d, n = d.return;
                  try {
                    t = r, C.props = t.memoizedProps, C.state = t.memoizedState, C.componentWillUnmount();
                  } catch (S) {
                    K(r, n, S);
                  }
                }
                break;
              case 5:
                Yt(d, d.return);
                break;
              case 22:
                if (d.memoizedState !== null) {
                  Ku(h);
                  continue;
                }
            }
            w !== null ? (w.return = d, j = w) : Ku(h);
          }
          m = m.sibling;
        }
        e: for (m = null, h = e; ; ) {
          if (h.tag === 5) {
            if (m === null) {
              m = h;
              try {
                l = h.stateNode, c ? (o = l.style, typeof o.setProperty == "function" ? o.setProperty("display", "none", "important") : o.display = "none") : (u = h.stateNode, s = h.memoizedProps.style, i = s != null && s.hasOwnProperty("display") ? s.display : null, u.style.display = Ts("display", i));
              } catch (S) {
                K(e, e.return, S);
              }
            }
          } else if (h.tag === 6) {
            if (m === null) try {
              h.stateNode.nodeValue = c ? "" : h.memoizedProps;
            } catch (S) {
              K(e, e.return, S);
            }
          } else if ((h.tag !== 22 && h.tag !== 23 || h.memoizedState === null || h === e) && h.child !== null) {
            h.child.return = h, h = h.child;
            continue;
          }
          if (h === e) break e;
          for (; h.sibling === null; ) {
            if (h.return === null || h.return === e) break e;
            m === h && (m = null), h = h.return;
          }
          m === h && (m = null), h.sibling.return = h.return, h = h.sibling;
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
          if (nc(n)) {
            var r = n;
            break e;
          }
          n = n.return;
        }
        throw Error(k(160));
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
          throw Error(k(161));
      }
    } catch (s) {
      K(e, e.return, s);
    }
    e.flags &= -3;
  }
  t & 4096 && (e.flags &= -4097);
}
function Id(e, t, n) {
  j = e, oc(e);
}
function oc(e, t, n) {
  for (var r = (e.mode & 1) !== 0; j !== null; ) {
    var l = j, o = l.child;
    if (l.tag === 22 && r) {
      var i = l.memoizedState !== null || xr;
      if (!i) {
        var u = l.alternate, s = u !== null && u.memoizedState !== null || ie;
        u = xr;
        var c = ie;
        if (xr = i, (ie = s) && !c) for (j = l; j !== null; ) i = j, s = i.child, i.tag === 22 && i.memoizedState !== null ? Yu(l) : s !== null ? (s.return = i, j = s) : Yu(l);
        for (; o !== null; ) j = o, oc(o), o = o.sibling;
        j = l, xr = u, ie = c;
      }
      Wu(e);
    } else l.subtreeFlags & 8772 && o !== null ? (o.return = l, j = o) : Wu(e);
  }
}
function Wu(e) {
  for (; j !== null; ) {
    var t = j;
    if (t.flags & 8772) {
      var n = t.alternate;
      try {
        if (t.flags & 8772) switch (t.tag) {
          case 0:
          case 11:
          case 15:
            ie || vl(5, t);
            break;
          case 1:
            var r = t.stateNode;
            if (t.flags & 4 && !ie) if (n === null) r.componentDidMount();
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
                var m = c.memoizedState;
                if (m !== null) {
                  var h = m.dehydrated;
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
            throw Error(k(163));
        }
        ie || t.flags & 512 && $o(t);
      } catch (d) {
        K(t, t.return, d);
      }
    }
    if (t === e) {
      j = null;
      break;
    }
    if (n = t.sibling, n !== null) {
      n.return = t.return, j = n;
      break;
    }
    j = t.return;
  }
}
function Ku(e) {
  for (; j !== null; ) {
    var t = j;
    if (t === e) {
      j = null;
      break;
    }
    var n = t.sibling;
    if (n !== null) {
      n.return = t.return, j = n;
      break;
    }
    j = t.return;
  }
}
function Yu(e) {
  for (; j !== null; ) {
    var t = j;
    try {
      switch (t.tag) {
        case 0:
        case 11:
        case 15:
          var n = t.return;
          try {
            vl(4, t);
          } catch (s) {
            K(t, n, s);
          }
          break;
        case 1:
          var r = t.stateNode;
          if (typeof r.componentDidMount == "function") {
            var l = t.return;
            try {
              r.componentDidMount();
            } catch (s) {
              K(t, l, s);
            }
          }
          var o = t.return;
          try {
            $o(t);
          } catch (s) {
            K(t, o, s);
          }
          break;
        case 5:
          var i = t.return;
          try {
            $o(t);
          } catch (s) {
            K(t, i, s);
          }
      }
    } catch (s) {
      K(t, t.return, s);
    }
    if (t === e) {
      j = null;
      break;
    }
    var u = t.sibling;
    if (u !== null) {
      u.return = t.return, j = u;
      break;
    }
    j = t.return;
  }
}
var Dd = Math.ceil, nl = be.ReactCurrentDispatcher, zi = be.ReactCurrentOwner, je = be.ReactCurrentBatchConfig, I = 0, ee = null, G = null, ne = 0, ge = 0, Xt = yt(0), J = 0, qn = null, Rt = 0, gl = 0, Ri = 0, In = null, de = null, Mi = 0, sn = 1 / 0, Qe = null, rl = !1, Qo = null, dt = null, Er = !1, it = null, ll = 0, Dn = 0, Wo = null, Ir = -1, Dr = 0;
function ae() {
  return I & 6 ? X() : Ir !== -1 ? Ir : Ir = X();
}
function pt(e) {
  return e.mode & 1 ? I & 2 && ne !== 0 ? ne & -ne : yd.transition !== null ? (Dr === 0 && (Dr = Hs()), Dr) : (e = O, e !== 0 || (e = window.event, e = e === void 0 ? 16 : Zs(e.type)), e) : 1;
}
function Fe(e, t, n, r) {
  if (50 < Dn) throw Dn = 0, Wo = null, Error(k(185));
  tr(e, n, r), (!(I & 2) || e !== ee) && (e === ee && (!(I & 2) && (gl |= n), J === 4 && lt(e, ne)), ve(e, r), n === 1 && I === 0 && !(t.mode & 1) && (sn = X() + 500, pl && wt()));
}
function ve(e, t) {
  var n = e.callbackNode;
  yf(e, t);
  var r = $r(e, e === ee ? ne : 0);
  if (r === 0) n !== null && nu(n), e.callbackNode = null, e.callbackPriority = 0;
  else if (t = r & -r, e.callbackPriority !== t) {
    if (n != null && nu(n), t === 1) e.tag === 0 ? gd(Xu.bind(null, e)) : ha(Xu.bind(null, e)), pd(function() {
      !(I & 6) && wt();
    }), n = null;
    else {
      switch (Qs(r)) {
        case 1:
          n = oi;
          break;
        case 4:
          n = $s;
          break;
        case 16:
          n = Vr;
          break;
        case 536870912:
          n = Bs;
          break;
        default:
          n = Vr;
      }
      n = pc(n, ic.bind(null, e));
    }
    e.callbackPriority = t, e.callbackNode = n;
  }
}
function ic(e, t) {
  if (Ir = -1, Dr = 0, I & 6) throw Error(k(327));
  var n = e.callbackNode;
  if (en() && e.callbackNode !== n) return null;
  var r = $r(e, e === ee ? ne : 0);
  if (r === 0) return null;
  if (r & 30 || r & e.expiredLanes || t) t = ol(e, r);
  else {
    t = r;
    var l = I;
    I |= 2;
    var o = sc();
    (ee !== e || ne !== t) && (Qe = null, sn = X() + 500, Pt(e, t));
    do
      try {
        Ud();
        break;
      } catch (u) {
        uc(e, u);
      }
    while (!0);
    yi(), nl.current = o, I = l, G !== null ? t = 0 : (ee = null, ne = 0, t = J);
  }
  if (t !== 0) {
    if (t === 2 && (l = yo(e), l !== 0 && (r = l, t = Ko(e, l))), t === 1) throw n = qn, Pt(e, 0), lt(e, r), ve(e, X()), n;
    if (t === 6) lt(e, r);
    else {
      if (l = e.current.alternate, !(r & 30) && !Od(l) && (t = ol(e, r), t === 2 && (o = yo(e), o !== 0 && (r = o, t = Ko(e, o))), t === 1)) throw n = qn, Pt(e, 0), lt(e, r), ve(e, X()), n;
      switch (e.finishedWork = l, e.finishedLanes = r, t) {
        case 0:
        case 1:
          throw Error(k(345));
        case 2:
          Et(e, de, Qe);
          break;
        case 3:
          if (lt(e, r), (r & 130023424) === r && (t = Mi + 500 - X(), 10 < t)) {
            if ($r(e, 0) !== 0) break;
            if (l = e.suspendedLanes, (l & r) !== r) {
              ae(), e.pingedLanes |= e.suspendedLanes & l;
              break;
            }
            e.timeoutHandle = No(Et.bind(null, e, de, Qe), t);
            break;
          }
          Et(e, de, Qe);
          break;
        case 4:
          if (lt(e, r), (r & 4194240) === r) break;
          for (t = e.eventTimes, l = -1; 0 < r; ) {
            var i = 31 - Oe(r);
            o = 1 << i, i = t[i], i > l && (l = i), r &= ~o;
          }
          if (r = l, r = X() - r, r = (120 > r ? 120 : 480 > r ? 480 : 1080 > r ? 1080 : 1920 > r ? 1920 : 3e3 > r ? 3e3 : 4320 > r ? 4320 : 1960 * Dd(r / 1960)) - r, 10 < r) {
            e.timeoutHandle = No(Et.bind(null, e, de, Qe), r);
            break;
          }
          Et(e, de, Qe);
          break;
        case 5:
          Et(e, de, Qe);
          break;
        default:
          throw Error(k(329));
      }
    }
  }
  return ve(e, X()), e.callbackNode === n ? ic.bind(null, e) : null;
}
function Ko(e, t) {
  var n = In;
  return e.current.memoizedState.isDehydrated && (Pt(e, t).flags |= 256), e = ol(e, t), e !== 2 && (t = de, de = n, t !== null && Yo(t)), e;
}
function Yo(e) {
  de === null ? de = e : de.push.apply(de, e);
}
function Od(e) {
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
  if (I & 6) throw Error(k(327));
  en();
  var t = $r(e, 0);
  if (!(t & 1)) return ve(e, X()), null;
  var n = ol(e, t);
  if (e.tag !== 0 && n === 2) {
    var r = yo(e);
    r !== 0 && (t = r, n = Ko(e, r));
  }
  if (n === 1) throw n = qn, Pt(e, 0), lt(e, t), ve(e, X()), n;
  if (n === 6) throw Error(k(345));
  return e.finishedWork = e.current.alternate, e.finishedLanes = t, Et(e, de, Qe), ve(e, X()), null;
}
function Ii(e, t) {
  var n = I;
  I |= 1;
  try {
    return e(t);
  } finally {
    I = n, I === 0 && (sn = X() + 500, pl && wt());
  }
}
function Mt(e) {
  it !== null && it.tag === 0 && !(I & 6) && en();
  var t = I;
  I |= 1;
  var n = je.transition, r = O;
  try {
    if (je.transition = null, O = 1, e) return e();
  } finally {
    O = r, je.transition = n, I = t, !(I & 6) && wt();
  }
}
function Di() {
  ge = Xt.current, A(Xt);
}
function Pt(e, t) {
  e.finishedWork = null, e.finishedLanes = 0;
  var n = e.timeoutHandle;
  if (n !== -1 && (e.timeoutHandle = -1, dd(n)), G !== null) for (n = G.return; n !== null; ) {
    var r = n;
    switch (hi(r), r.tag) {
      case 1:
        r = r.type.childContextTypes, r != null && Kr();
        break;
      case 3:
        on(), A(me), A(ue), Ci();
        break;
      case 5:
        Ei(r);
        break;
      case 4:
        on();
        break;
      case 13:
        A(B);
        break;
      case 19:
        A(B);
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
  if (ee = e, G = e = mt(e.current, null), ne = ge = t, J = 0, qn = null, Ri = gl = Rt = 0, de = In = null, _t !== null) {
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
function uc(e, t) {
  do {
    var n = G;
    try {
      if (yi(), zr.current = tl, el) {
        for (var r = H.memoizedState; r !== null; ) {
          var l = r.queue;
          l !== null && (l.pending = null), r = r.next;
        }
        el = !1;
      }
      if (zt = 0, b = Z = H = null, Rn = !1, Gn = 0, zi.current = null, n === null || n.return === null) {
        J = 1, qn = t, G = null;
        break;
      }
      e: {
        var o = e, i = n.return, u = n, s = t;
        if (t = ne, u.flags |= 32768, s !== null && typeof s == "object" && typeof s.then == "function") {
          var c = s, m = u, h = m.tag;
          if (!(m.mode & 1) && (h === 0 || h === 11 || h === 15)) {
            var d = m.alternate;
            d ? (m.updateQueue = d.updateQueue, m.memoizedState = d.memoizedState, m.lanes = d.lanes) : (m.updateQueue = null, m.memoizedState = null);
          }
          var w = Du(i);
          if (w !== null) {
            w.flags &= -257, Ou(w, i, u, o, t), w.mode & 1 && Iu(o, c, t), t = w, s = c;
            var C = t.updateQueue;
            if (C === null) {
              var S = /* @__PURE__ */ new Set();
              S.add(s), t.updateQueue = S;
            } else C.add(s);
            break e;
          } else {
            if (!(t & 1)) {
              Iu(o, c, t), Oi();
              break e;
            }
            s = Error(k(426));
          }
        } else if (V && u.mode & 1) {
          var z = Du(i);
          if (z !== null) {
            !(z.flags & 65536) && (z.flags |= 256), Ou(z, i, u, o, t), vi(un(s, u));
            break e;
          }
        }
        o = s = un(s, u), J !== 4 && (J = 2), In === null ? In = [o] : In.push(o), o = i;
        do {
          switch (o.tag) {
            case 3:
              o.flags |= 65536, t &= -t, o.lanes |= t;
              var f = Qa(o, s, t);
              ju(o, f);
              break e;
            case 1:
              u = s;
              var a = o.type, p = o.stateNode;
              if (!(o.flags & 128) && (typeof a.getDerivedStateFromError == "function" || p !== null && typeof p.componentDidCatch == "function" && (dt === null || !dt.has(p)))) {
                o.flags |= 65536, t &= -t, o.lanes |= t;
                var y = Wa(o, u, t);
                ju(o, y);
                break e;
              }
          }
          o = o.return;
        } while (o !== null);
      }
      cc(n);
    } catch (_) {
      t = _, G === n && n !== null && (G = n = n.return);
      continue;
    }
    break;
  } while (!0);
}
function sc() {
  var e = nl.current;
  return nl.current = tl, e === null ? tl : e;
}
function Oi() {
  (J === 0 || J === 3 || J === 2) && (J = 4), ee === null || !(Rt & 268435455) && !(gl & 268435455) || lt(ee, ne);
}
function ol(e, t) {
  var n = I;
  I |= 2;
  var r = sc();
  (ee !== e || ne !== t) && (Qe = null, Pt(e, t));
  do
    try {
      Fd();
      break;
    } catch (l) {
      uc(e, l);
    }
  while (!0);
  if (yi(), I = n, nl.current = r, G !== null) throw Error(k(261));
  return ee = null, ne = 0, J;
}
function Fd() {
  for (; G !== null; ) ac(G);
}
function Ud() {
  for (; G !== null && !af(); ) ac(G);
}
function ac(e) {
  var t = dc(e.alternate, e, ge);
  e.memoizedProps = e.pendingProps, t === null ? cc(e) : G = t, zi.current = null;
}
function cc(e) {
  var t = e;
  do {
    var n = t.alternate;
    if (e = t.return, t.flags & 32768) {
      if (n = zd(n, t), n !== null) {
        n.flags &= 32767, G = n;
        return;
      }
      if (e !== null) e.flags |= 32768, e.subtreeFlags = 0, e.deletions = null;
      else {
        J = 6, G = null;
        return;
      }
    } else if (n = Td(n, t, ge), n !== null) {
      G = n;
      return;
    }
    if (t = t.sibling, t !== null) {
      G = t;
      return;
    }
    G = t = e;
  } while (t !== null);
  J === 0 && (J = 5);
}
function Et(e, t, n) {
  var r = O, l = je.transition;
  try {
    je.transition = null, O = 1, Ad(e, t, n, r);
  } finally {
    je.transition = l, O = r;
  }
  return null;
}
function Ad(e, t, n, r) {
  do
    en();
  while (it !== null);
  if (I & 6) throw Error(k(327));
  n = e.finishedWork;
  var l = e.finishedLanes;
  if (n === null) return null;
  if (e.finishedWork = null, e.finishedLanes = 0, n === e.current) throw Error(k(177));
  e.callbackNode = null, e.callbackPriority = 0;
  var o = n.lanes | n.childLanes;
  if (wf(e, o), e === ee && (G = ee = null, ne = 0), !(n.subtreeFlags & 2064) && !(n.flags & 2064) || Er || (Er = !0, pc(Vr, function() {
    return en(), null;
  })), o = (n.flags & 15990) !== 0, n.subtreeFlags & 15990 || o) {
    o = je.transition, je.transition = null;
    var i = O;
    O = 1;
    var u = I;
    I |= 4, zi.current = null, Md(e, n), lc(n, e), od(Co), Br = !!Eo, Co = Eo = null, e.current = n, Id(n), cf(), I = u, O = i, je.transition = o;
  } else e.current = n;
  if (Er && (Er = !1, it = e, ll = l), o = e.pendingLanes, o === 0 && (dt = null), pf(n.stateNode), ve(e, X()), t !== null) for (r = e.onRecoverableError, n = 0; n < t.length; n++) l = t[n], r(l.value, { componentStack: l.stack, digest: l.digest });
  if (rl) throw rl = !1, e = Qo, Qo = null, e;
  return ll & 1 && e.tag !== 0 && en(), o = e.pendingLanes, o & 1 ? e === Wo ? Dn++ : (Dn = 0, Wo = e) : Dn = 0, wt(), null;
}
function en() {
  if (it !== null) {
    var e = Qs(ll), t = je.transition, n = O;
    try {
      if (je.transition = null, O = 16 > e ? 16 : e, it === null) var r = !1;
      else {
        if (e = it, it = null, ll = 0, I & 6) throw Error(k(331));
        var l = I;
        for (I |= 4, j = e.current; j !== null; ) {
          var o = j, i = o.child;
          if (j.flags & 16) {
            var u = o.deletions;
            if (u !== null) {
              for (var s = 0; s < u.length; s++) {
                var c = u[s];
                for (j = c; j !== null; ) {
                  var m = j;
                  switch (m.tag) {
                    case 0:
                    case 11:
                    case 15:
                      Mn(8, m, o);
                  }
                  var h = m.child;
                  if (h !== null) h.return = m, j = h;
                  else for (; j !== null; ) {
                    m = j;
                    var d = m.sibling, w = m.return;
                    if (tc(m), m === c) {
                      j = null;
                      break;
                    }
                    if (d !== null) {
                      d.return = w, j = d;
                      break;
                    }
                    j = w;
                  }
                }
              }
              var C = o.alternate;
              if (C !== null) {
                var S = C.child;
                if (S !== null) {
                  C.child = null;
                  do {
                    var z = S.sibling;
                    S.sibling = null, S = z;
                  } while (S !== null);
                }
              }
              j = o;
            }
          }
          if (o.subtreeFlags & 2064 && i !== null) i.return = o, j = i;
          else e: for (; j !== null; ) {
            if (o = j, o.flags & 2048) switch (o.tag) {
              case 0:
              case 11:
              case 15:
                Mn(9, o, o.return);
            }
            var f = o.sibling;
            if (f !== null) {
              f.return = o.return, j = f;
              break e;
            }
            j = o.return;
          }
        }
        var a = e.current;
        for (j = a; j !== null; ) {
          i = j;
          var p = i.child;
          if (i.subtreeFlags & 2064 && p !== null) p.return = i, j = p;
          else e: for (i = a; j !== null; ) {
            if (u = j, u.flags & 2048) try {
              switch (u.tag) {
                case 0:
                case 11:
                case 15:
                  vl(9, u);
              }
            } catch (_) {
              K(u, u.return, _);
            }
            if (u === i) {
              j = null;
              break e;
            }
            var y = u.sibling;
            if (y !== null) {
              y.return = u.return, j = y;
              break e;
            }
            j = u.return;
          }
        }
        if (I = l, wt(), Be && typeof Be.onPostCommitFiberRoot == "function") try {
          Be.onPostCommitFiberRoot(sl, e);
        } catch {
        }
        r = !0;
      }
      return r;
    } finally {
      O = n, je.transition = t;
    }
  }
  return !1;
}
function Gu(e, t, n) {
  t = un(n, t), t = Qa(e, t, 1), e = ft(e, t, 1), t = ae(), e !== null && (tr(e, 1, t), ve(e, t));
}
function K(e, t, n) {
  if (e.tag === 3) Gu(e, e, n);
  else for (; t !== null; ) {
    if (t.tag === 3) {
      Gu(t, e, n);
      break;
    } else if (t.tag === 1) {
      var r = t.stateNode;
      if (typeof t.type.getDerivedStateFromError == "function" || typeof r.componentDidCatch == "function" && (dt === null || !dt.has(r))) {
        e = un(n, e), e = Wa(t, e, 1), t = ft(t, e, 1), e = ae(), t !== null && (tr(t, 1, e), ve(t, e));
        break;
      }
    }
    t = t.return;
  }
}
function Vd(e, t, n) {
  var r = e.pingCache;
  r !== null && r.delete(t), t = ae(), e.pingedLanes |= e.suspendedLanes & n, ee === e && (ne & n) === n && (J === 4 || J === 3 && (ne & 130023424) === ne && 500 > X() - Mi ? Pt(e, 0) : Ri |= n), ve(e, t);
}
function fc(e, t) {
  t === 0 && (e.mode & 1 ? (t = pr, pr <<= 1, !(pr & 130023424) && (pr = 4194304)) : t = 1);
  var n = ae();
  e = Je(e, t), e !== null && (tr(e, t, n), ve(e, n));
}
function $d(e) {
  var t = e.memoizedState, n = 0;
  t !== null && (n = t.retryLane), fc(e, n);
}
function Bd(e, t) {
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
      throw Error(k(314));
  }
  r !== null && r.delete(t), fc(e, n);
}
var dc;
dc = function(e, t, n) {
  if (e !== null) if (e.memoizedProps !== t.pendingProps || me.current) pe = !0;
  else {
    if (!(e.lanes & n) && !(t.flags & 128)) return pe = !1, Ld(e, t, n);
    pe = !!(e.flags & 131072);
  }
  else pe = !1, V && t.flags & 1048576 && va(t, Gr, t.index);
  switch (t.lanes = 0, t.tag) {
    case 2:
      var r = t.type;
      Mr(e, t), e = t.pendingProps;
      var l = nn(t, ue.current);
      bt(t, n), l = Ni(null, t, r, e, l, n);
      var o = Pi();
      return t.flags |= 1, typeof l == "object" && l !== null && typeof l.render == "function" && l.$$typeof === void 0 ? (t.tag = 1, t.memoizedState = null, t.updateQueue = null, he(r) ? (o = !0, Yr(t)) : o = !1, t.memoizedState = l.state !== null && l.state !== void 0 ? l.state : null, Si(t), l.updater = hl, t.stateNode = l, l._reactInternals = t, Mo(t, r, e, n), t = Oo(null, t, r, !0, o, n)) : (t.tag = 0, V && o && mi(t), se(null, t, l, n), t = t.child), t;
    case 16:
      r = t.elementType;
      e: {
        switch (Mr(e, t), e = t.pendingProps, l = r._init, r = l(r._payload), t.type = r, l = t.tag = Qd(r), e = Me(r, e), l) {
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
        throw Error(k(
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
        if (Ga(t), e === null) throw Error(k(387));
        r = t.pendingProps, o = t.memoizedState, l = o.element, xa(e, t), qr(t, r, null, n);
        var i = t.memoizedState;
        if (r = i.element, o.isDehydrated) if (o = { element: r, isDehydrated: !1, cache: i.cache, pendingSuspenseBoundaries: i.pendingSuspenseBoundaries, transitions: i.transitions }, t.updateQueue.baseState = o, t.memoizedState = o, t.flags & 256) {
          l = un(Error(k(423)), t), t = Vu(e, t, r, n, l);
          break e;
        } else if (r !== l) {
          l = un(Error(k(424)), t), t = Vu(e, t, r, n, l);
          break e;
        } else for (ye = ct(t.stateNode.containerInfo.firstChild), we = t, V = !0, De = null, n = ka(t, null, r, n), t.child = n; n; ) n.flags = n.flags & -3 | 4096, n = n.sibling;
        else {
          if (rn(), r === l) {
            t = qe(e, t, n);
            break e;
          }
          se(e, t, r, n);
        }
        t = t.child;
      }
      return t;
    case 5:
      return Ea(t), e === null && To(t), r = t.type, l = t.pendingProps, o = e !== null ? e.memoizedProps : null, i = l.children, _o(r, l) ? i = null : o !== null && _o(r, o) && (t.flags |= 32), Xa(e, t), se(e, t, i, n), t.child;
    case 6:
      return e === null && To(t), null;
    case 13:
      return Za(e, t, n);
    case 4:
      return xi(t, t.stateNode.containerInfo), r = t.pendingProps, e === null ? t.child = ln(t, null, r, n) : se(e, t, r, n), t.child;
    case 11:
      return r = t.type, l = t.pendingProps, l = t.elementType === r ? l : Me(r, l), Fu(e, t, r, l, n);
    case 7:
      return se(e, t, t.pendingProps, n), t.child;
    case 8:
      return se(e, t, t.pendingProps.children, n), t.child;
    case 12:
      return se(e, t, t.pendingProps.children, n), t.child;
    case 10:
      e: {
        if (r = t.type._context, l = t.pendingProps, o = t.memoizedProps, i = l.value, F(Zr, r._currentValue), r._currentValue = i, o !== null) if (Ue(o.value, i)) {
          if (o.children === l.children && !me.current) {
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
                    var m = c.pending;
                    m === null ? s.next = s : (s.next = m.next, m.next = s), c.pending = s;
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
            if (i = o.return, i === null) throw Error(k(341));
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
        se(e, t, l.children, n), t = t.child;
      }
      return t;
    case 9:
      return l = t.type, r = t.pendingProps.children, bt(t, n), l = Le(l), r = r(l), t.flags |= 1, se(e, t, r, n), t.child;
    case 14:
      return r = t.type, l = Me(r, t.pendingProps), l = Me(r.type, l), Uu(e, t, r, l, n);
    case 15:
      return Ka(e, t, t.type, t.pendingProps, n);
    case 17:
      return r = t.type, l = t.pendingProps, l = t.elementType === r ? l : Me(r, l), Mr(e, t), t.tag = 1, he(r) ? (e = !0, Yr(t)) : e = !1, bt(t, n), Ha(t, r, l), Mo(t, r, l, n), Oo(null, t, r, !0, e, n);
    case 19:
      return Ja(e, t, n);
    case 22:
      return Ya(e, t, n);
  }
  throw Error(k(156, t.tag));
};
function pc(e, t) {
  return Vs(e, t);
}
function Hd(e, t, n, r) {
  this.tag = e, this.key = n, this.sibling = this.child = this.return = this.stateNode = this.type = this.elementType = null, this.index = 0, this.ref = null, this.pendingProps = t, this.dependencies = this.memoizedState = this.updateQueue = this.memoizedProps = null, this.mode = r, this.subtreeFlags = this.flags = 0, this.deletions = null, this.childLanes = this.lanes = 0, this.alternate = null;
}
function Pe(e, t, n, r) {
  return new Hd(e, t, n, r);
}
function Fi(e) {
  return e = e.prototype, !(!e || !e.isReactComponent);
}
function Qd(e) {
  if (typeof e == "function") return Fi(e) ? 1 : 0;
  if (e != null) {
    if (e = e.$$typeof, e === ni) return 11;
    if (e === ri) return 14;
  }
  return 2;
}
function mt(e, t) {
  var n = e.alternate;
  return n === null ? (n = Pe(e.tag, t, e.key, e.mode), n.elementType = e.elementType, n.type = e.type, n.stateNode = e.stateNode, n.alternate = e, e.alternate = n) : (n.pendingProps = t, n.type = e.type, n.flags = 0, n.subtreeFlags = 0, n.deletions = null), n.flags = e.flags & 14680064, n.childLanes = e.childLanes, n.lanes = e.lanes, n.child = e.child, n.memoizedProps = e.memoizedProps, n.memoizedState = e.memoizedState, n.updateQueue = e.updateQueue, t = e.dependencies, n.dependencies = t === null ? null : { lanes: t.lanes, firstContext: t.firstContext }, n.sibling = e.sibling, n.index = e.index, n.ref = e.ref, n;
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
      return e = Pe(12, n, t, l | 2), e.elementType = no, e.lanes = o, e;
    case ro:
      return e = Pe(13, n, t, l), e.elementType = ro, e.lanes = o, e;
    case lo:
      return e = Pe(19, n, t, l), e.elementType = lo, e.lanes = o, e;
    case Es:
      return yl(n, l, o, t);
    default:
      if (typeof e == "object" && e !== null) switch (e.$$typeof) {
        case Ss:
          i = 10;
          break e;
        case xs:
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
      throw Error(k(130, e == null ? e : typeof e, ""));
  }
  return t = Pe(i, n, t, l), t.elementType = e, t.type = r, t.lanes = o, t;
}
function jt(e, t, n, r) {
  return e = Pe(7, e, r, t), e.lanes = n, e;
}
function yl(e, t, n, r) {
  return e = Pe(22, e, r, t), e.elementType = Es, e.lanes = n, e.stateNode = { isHidden: !1 }, e;
}
function Gl(e, t, n) {
  return e = Pe(6, e, null, t), e.lanes = n, e;
}
function Zl(e, t, n) {
  return t = Pe(4, e.children !== null ? e.children : [], e.key, t), t.lanes = n, t.stateNode = { containerInfo: e.containerInfo, pendingChildren: null, implementation: e.implementation }, t;
}
function Wd(e, t, n, r, l) {
  this.tag = t, this.containerInfo = e, this.finishedWork = this.pingCache = this.current = this.pendingChildren = null, this.timeoutHandle = -1, this.callbackNode = this.pendingContext = this.context = null, this.callbackPriority = 0, this.eventTimes = Tl(0), this.expirationTimes = Tl(-1), this.entangledLanes = this.finishedLanes = this.mutableReadLanes = this.expiredLanes = this.pingedLanes = this.suspendedLanes = this.pendingLanes = 0, this.entanglements = Tl(0), this.identifierPrefix = r, this.onRecoverableError = l, this.mutableSourceEagerHydrationData = null;
}
function Ui(e, t, n, r, l, o, i, u, s) {
  return e = new Wd(e, t, n, u, s), t === 1 ? (t = 1, o === !0 && (t |= 8)) : t = 0, o = Pe(3, null, null, t), e.current = o, o.stateNode = e, o.memoizedState = { element: r, isDehydrated: n, cache: null, transitions: null, pendingSuspenseBoundaries: null }, Si(o), e;
}
function Kd(e, t, n) {
  var r = 3 < arguments.length && arguments[3] !== void 0 ? arguments[3] : null;
  return { $$typeof: Ft, key: r == null ? null : "" + r, children: e, containerInfo: t, implementation: n };
}
function mc(e) {
  if (!e) return vt;
  e = e._reactInternals;
  e: {
    if (Dt(e) !== e || e.tag !== 1) throw Error(k(170));
    var t = e;
    do {
      switch (t.tag) {
        case 3:
          t = t.stateNode.context;
          break e;
        case 1:
          if (he(t.type)) {
            t = t.stateNode.__reactInternalMemoizedMergedChildContext;
            break e;
          }
      }
      t = t.return;
    } while (t !== null);
    throw Error(k(171));
  }
  if (e.tag === 1) {
    var n = e.type;
    if (he(n)) return ma(e, n, t);
  }
  return t;
}
function hc(e, t, n, r, l, o, i, u, s) {
  return e = Ui(n, r, !0, e, l, o, i, u, s), e.context = mc(null), n = e.current, r = ae(), l = pt(n), o = Xe(r, l), o.callback = t ?? null, ft(n, o, l), e.current.lanes = l, tr(e, l, r), ve(e, r), e;
}
function wl(e, t, n, r) {
  var l = t.current, o = ae(), i = pt(l);
  return n = mc(n), t.context === null ? t.context = n : t.pendingContext = n, t = Xe(o, i), t.payload = { element: e }, r = r === void 0 ? null : r, r !== null && (t.callback = r), e = ft(l, t, i), e !== null && (Fe(e, l, i, o), Tr(e, l, i)), i;
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
function Yd() {
  return null;
}
var vc = typeof reportError == "function" ? reportError : function(e) {
  console.error(e);
};
function Vi(e) {
  this._internalRoot = e;
}
kl.prototype.render = Vi.prototype.render = function(e) {
  var t = this._internalRoot;
  if (t === null) throw Error(k(409));
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
    var t = Ys();
    e = { blockedOn: null, target: e, priority: t };
    for (var n = 0; n < rt.length && t !== 0 && t < rt[n].priority; n++) ;
    rt.splice(n, 0, e), n === 0 && Gs(e);
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
function Xd(e, t, n, r, l) {
  if (l) {
    if (typeof r == "function") {
      var o = r;
      r = function() {
        var c = il(i);
        o.call(c);
      };
    }
    var i = hc(t, r, e, 0, null, !1, !1, "", Ju);
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
  } else i = Xd(n, t, e, l, r);
  return il(i);
}
Ws = function(e) {
  switch (e.tag) {
    case 3:
      var t = e.stateNode;
      if (t.current.memoizedState.isDehydrated) {
        var n = _n(t.pendingLanes);
        n !== 0 && (ii(t, n | 1), ve(t, X()), !(I & 6) && (sn = X() + 500, wt()));
      }
      break;
    case 13:
      Mt(function() {
        var r = Je(e, 1);
        if (r !== null) {
          var l = ae();
          Fe(r, e, 1, l);
        }
      }), Ai(e, 1);
  }
};
ui = function(e) {
  if (e.tag === 13) {
    var t = Je(e, 134217728);
    if (t !== null) {
      var n = ae();
      Fe(t, e, 134217728, n);
    }
    Ai(e, 134217728);
  }
};
Ks = function(e) {
  if (e.tag === 13) {
    var t = pt(e), n = Je(e, t);
    if (n !== null) {
      var r = ae();
      Fe(n, e, t, r);
    }
    Ai(e, t);
  }
};
Ys = function() {
  return O;
};
Xs = function(e, t) {
  var n = O;
  try {
    return O = e, t();
  } finally {
    O = n;
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
            if (!l) throw Error(k(90));
            _s(r), uo(r, l);
          }
        }
      }
      break;
    case "textarea":
      Ps(e, n);
      break;
    case "select":
      t = n.value, t != null && Gt(e, !!n.multiple, t, !1);
  }
};
Is = Ii;
Ds = Mt;
var Gd = { usingClientEntryPoint: !1, Events: [rr, Bt, dl, Rs, Ms, Ii] }, xn = { findFiberByHostInstance: Ct, bundleType: 0, version: "18.3.1", rendererPackageName: "react-dom" }, Zd = { bundleType: xn.bundleType, version: xn.version, rendererPackageName: xn.rendererPackageName, rendererConfig: xn.rendererConfig, overrideHookState: null, overrideHookStateDeletePath: null, overrideHookStateRenamePath: null, overrideProps: null, overridePropsDeletePath: null, overridePropsRenamePath: null, setErrorHandler: null, setSuspenseHandler: null, scheduleUpdate: null, currentDispatcherRef: be.ReactCurrentDispatcher, findHostInstanceByFiber: function(e) {
  return e = Us(e), e === null ? null : e.stateNode;
}, findFiberByHostInstance: xn.findFiberByHostInstance || Yd, findHostInstancesForRefresh: null, scheduleRefresh: null, scheduleRoot: null, setRefreshHandler: null, getCurrentFiber: null, reconcilerVersion: "18.3.1-next-f1338f8080-20240426" };
if (typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ < "u") {
  var Cr = __REACT_DEVTOOLS_GLOBAL_HOOK__;
  if (!Cr.isDisabled && Cr.supportsFiber) try {
    sl = Cr.inject(Zd), Be = Cr;
  } catch {
  }
}
Se.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED = Gd;
Se.createPortal = function(e, t) {
  var n = 2 < arguments.length && arguments[2] !== void 0 ? arguments[2] : null;
  if (!$i(t)) throw Error(k(200));
  return Kd(e, t, null, n);
};
Se.createRoot = function(e, t) {
  if (!$i(e)) throw Error(k(299));
  var n = !1, r = "", l = vc;
  return t != null && (t.unstable_strictMode === !0 && (n = !0), t.identifierPrefix !== void 0 && (r = t.identifierPrefix), t.onRecoverableError !== void 0 && (l = t.onRecoverableError)), t = Ui(e, 1, !1, null, null, n, !1, r, l), e[Ze] = t.current, Qn(e.nodeType === 8 ? e.parentNode : e), new Vi(t);
};
Se.findDOMNode = function(e) {
  if (e == null) return null;
  if (e.nodeType === 1) return e;
  var t = e._reactInternals;
  if (t === void 0)
    throw typeof e.render == "function" ? Error(k(188)) : (e = Object.keys(e).join(","), Error(k(268, e)));
  return e = Us(t), e = e === null ? null : e.stateNode, e;
};
Se.flushSync = function(e) {
  return Mt(e);
};
Se.hydrate = function(e, t, n) {
  if (!Sl(t)) throw Error(k(200));
  return xl(null, e, t, !0, n);
};
Se.hydrateRoot = function(e, t, n) {
  if (!$i(e)) throw Error(k(405));
  var r = n != null && n.hydratedSources || null, l = !1, o = "", i = vc;
  if (n != null && (n.unstable_strictMode === !0 && (l = !0), n.identifierPrefix !== void 0 && (o = n.identifierPrefix), n.onRecoverableError !== void 0 && (i = n.onRecoverableError)), t = hc(t, null, e, 1, n ?? null, l, !1, o, i), e[Ze] = t.current, Qn(e), r) for (e = 0; e < r.length; e++) n = r[e], l = n._getVersion, l = l(n._source), t.mutableSourceEagerHydrationData == null ? t.mutableSourceEagerHydrationData = [n, l] : t.mutableSourceEagerHydrationData.push(
    n,
    l
  );
  return new kl(t);
};
Se.render = function(e, t, n) {
  if (!Sl(t)) throw Error(k(200));
  return xl(null, e, t, !1, n);
};
Se.unmountComponentAtNode = function(e) {
  if (!Sl(e)) throw Error(k(40));
  return e._reactRootContainer ? (Mt(function() {
    xl(null, null, e, !1, function() {
      e._reactRootContainer = null, e[Ze] = null;
    });
  }), !0) : !1;
};
Se.unstable_batchedUpdates = Ii;
Se.unstable_renderSubtreeIntoContainer = function(e, t, n, r) {
  if (!Sl(n)) throw Error(k(200));
  if (e == null || e._reactInternals === void 0) throw Error(k(38));
  return xl(e, t, n, !1, r);
};
Se.version = "18.3.1-next-f1338f8080-20240426";
function gc() {
  if (!(typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ > "u" || typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE != "function"))
    try {
      __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE(gc);
    } catch (e) {
      console.error(e);
    }
}
gc(), gs.exports = Se;
var yc = gs.exports, wc, qu = yc;
wc = qu.createRoot, qu.hydrateRoot;
function Jl(...e) {
  return e.filter(Boolean).join(" ");
}
function Jd({
  content: e,
  children: t,
  className: n,
  triggerClassName: r,
  bubbleClassName: l,
  focusable: o = !1,
  ariaLabel: i,
  inline: u = !0
}) {
  const s = D.useRef(null), c = D.useRef(null), m = D.useRef(null), [h, d] = D.useState(!1), [w, C] = D.useState({}), [S, z] = D.useState("bottom"), f = D.useCallback(() => {
    const y = c.current, _ = m.current;
    if (!y || !_ || typeof window > "u")
      return;
    const N = 12, x = 12, g = y.getBoundingClientRect(), E = _.getBoundingClientRect(), L = Math.ceil(E.height), W = Math.ceil(E.width), Ee = g.left + g.width / 2 - W / 2, ze = Math.min(
      Math.max(N, Ee),
      Math.max(N, window.innerWidth - W - N)
    ), or = g.bottom + x + L + N <= window.innerHeight || g.top < L + x + N ? "bottom" : "top", pn = or === "bottom" ? g.bottom + x : Math.max(N, g.top - L - x), mn = g.left + g.width / 2, P = Math.min(
      Math.max(18, mn - ze),
      Math.max(18, W - 18)
    );
    z(or), C({
      left: ze,
      top: pn,
      "--admin-tooltip-arrow-left": `${P}px`
    });
  }, []);
  D.useLayoutEffect(() => {
    if (!h)
      return;
    f();
    const y = () => f();
    return window.addEventListener("resize", y), window.addEventListener("scroll", y, !0), () => {
      window.removeEventListener("resize", y), window.removeEventListener("scroll", y, !0);
    };
  }, [f, h]);
  const a = u ? "span" : "div", p = u ? "span" : "div";
  return /* @__PURE__ */ v.jsxs(
    a,
    {
      className: Jl("admin-tooltip", n),
      onMouseEnter: () => d(!0),
      onMouseLeave: () => d(!1),
      onFocusCapture: () => d(!0),
      onBlurCapture: (y) => {
        var _;
        (_ = s.current) != null && _.contains(y.relatedTarget) || d(!1);
      },
      ref: (y) => {
        s.current = y;
      },
      children: [
        /* @__PURE__ */ v.jsx(
          p,
          {
            className: Jl("admin-tooltip-trigger", r),
            ...o ? { tabIndex: 0, "aria-label": i } : {},
            ref: (y) => {
              c.current = y;
            },
            children: t
          }
        ),
        typeof document < "u" ? yc.createPortal(
          /* @__PURE__ */ v.jsx(
            "span",
            {
              ref: m,
              className: Jl("admin-tooltip-bubble", `placement-${S}`, l, h && "visible"),
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
function qd(e) {
  return typeof e == "object" && e !== null && !Array.isArray(e);
}
function bd(e) {
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
function kc(e = "", t = "") {
  return {
    id: bd("map-row"),
    key: e,
    value: t
  };
}
function es(e) {
  return qd(e) ? Object.entries(e).map(([t, n]) => kc(t, String(n ?? ""))) : [];
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
  return /* @__PURE__ */ v.jsx("div", { className: "empty-state", children: e ?? t });
}
function Sc(e, t, n) {
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
  validateItem: m,
  normalizeItem: h
}) {
  const d = D.useId(), w = ns(e, h), C = bu(w), [S, z] = D.useState(() => w.map((x) => ql(x))), f = D.useRef(C), a = D.useRef(null);
  D.useEffect(() => {
    Sc(f, a, C) && z(w.map((x) => ql(x)));
  }, [w, C]);
  function p(x) {
    const g = ns(
      x.map((E) => E.value),
      h
    );
    a.current = bu(g), t(g);
  }
  const _ = (c ?? []).map((x) => String(x)), N = _.length > 0 ? bn(_) : [];
  return /* @__PURE__ */ v.jsxs("div", { className: ["string-list-selector", "structured-input-editor", r].filter(Boolean).join(" "), children: [
    l ? /* @__PURE__ */ v.jsx("div", { className: "structured-input-description", children: l }) : null,
    /* @__PURE__ */ v.jsx("div", { className: "inline-actions compact-actions", children: /* @__PURE__ */ v.jsx(
      "button",
      {
        type: "button",
        onClick: () => {
          const x = [...S, ql()];
          z(x);
        },
        disabled: n,
        children: i
      }
    ) }),
    S.length === 0 ? Bi(o, `No ${u.toLowerCase()}s are configured.`) : null,
    S.map((x, g) => {
      const E = (m == null ? void 0 : m(x.value, g, S.map((L) => L.value))) ?? null;
      return /* @__PURE__ */ v.jsxs("div", { className: "string-list-selector-row structured-input-row", children: [
        /* @__PURE__ */ v.jsxs("label", { className: "field-stack structured-input-field", children: [
          /* @__PURE__ */ v.jsx("span", { children: u }),
          /* @__PURE__ */ v.jsx(
            "input",
            {
              list: N.length > 0 ? d : void 0,
              value: x.value,
              onChange: (L) => {
                const W = h ? h(L.target.value) : L.target.value, Ee = S.map(
                  (ze, dn) => dn === g ? { ...ze, value: W } : ze
                );
                z(Ee), p(Ee);
              },
              placeholder: s,
              disabled: n
            }
          ),
          E ? /* @__PURE__ */ v.jsx("div", { className: "structured-input-error", children: E }) : null
        ] }),
        /* @__PURE__ */ v.jsx("div", { className: "inline-actions compact-actions", children: /* @__PURE__ */ v.jsx(
          "button",
          {
            type: "button",
            className: "danger",
            onClick: () => {
              const L = S.filter((W, Ee) => Ee !== g);
              z(L), p(L);
            },
            disabled: n,
            children: "Remove"
          }
        ) })
      ] }, x.id);
    }),
    N.length > 0 ? /* @__PURE__ */ v.jsx("datalist", { id: d, children: N.map((x) => /* @__PURE__ */ v.jsx("option", { value: x }, x)) }) : null
  ] });
}
function ep({
  values: e,
  options: t,
  onChange: n,
  disabled: r = !1,
  className: l,
  description: o,
  emptyState: i,
  unknownLabel: u = "unknown value"
}) {
  const s = bn(e), c = new Set(t.map((d) => d.value)), m = s.filter((d) => !c.has(d));
  function h(d) {
    n(bn(d));
  }
  return /* @__PURE__ */ v.jsxs("div", { className: ["structured-input-editor", l].filter(Boolean).join(" "), children: [
    o ? /* @__PURE__ */ v.jsx("div", { className: "structured-input-description", children: o }) : null,
    /* @__PURE__ */ v.jsx("div", { className: "structured-input-option-grid", children: t.map((d) => {
      const w = s.includes(d.value);
      return /* @__PURE__ */ v.jsxs("label", { className: "checkbox-row structured-input-option-row", children: [
        /* @__PURE__ */ v.jsx(
          "input",
          {
            type: "checkbox",
            checked: w,
            onChange: (C) => {
              const S = C.target.checked ? [...s, d.value] : s.filter((z) => z !== d.value);
              h(S);
            },
            disabled: r
          }
        ),
        /* @__PURE__ */ v.jsx("span", { children: d.label })
      ] }, d.value);
    }) }),
    s.length === 0 ? Bi(i, "No selections are configured.") : null,
    m.map((d) => /* @__PURE__ */ v.jsxs("div", { className: "structured-input-unknown-row", children: [
      /* @__PURE__ */ v.jsxs("div", { className: "structured-input-unknown-copy", children: [
        /* @__PURE__ */ v.jsx("strong", { children: d }),
        /* @__PURE__ */ v.jsx("span", { className: "status-pill", children: u })
      ] }),
      /* @__PURE__ */ v.jsx("div", { className: "inline-actions compact-actions", children: /* @__PURE__ */ v.jsx(
        "button",
        {
          type: "button",
          className: "danger",
          onClick: () => h(s.filter((w) => w !== d)),
          disabled: r,
          children: "Remove"
        }
      ) })
    ] }, d))
  ] });
}
function tp({
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
  valueLabel: m = "Value"
}) {
  const h = JSON.stringify(e ?? {}), [d, w] = D.useState(() => es(e)), [C, S] = D.useState(null), z = D.useRef(h), f = D.useRef(null);
  D.useEffect(() => {
    Sc(z, f, h) && (w(es(e)), S(null));
  }, [h, e]);
  function a(y) {
    try {
      const N = ts(y, r, !1) ?? {};
      f.current = JSON.stringify(N), t(N), S(null);
    } catch (_) {
      S(_ instanceof Error ? _.message : String(_));
    }
  }
  const p = C ?? (() => {
    try {
      return ts(d, r, !1), null;
    } catch (y) {
      return y instanceof Error ? y.message : String(y);
    }
  })();
  return D.useEffect(() => {
    n == null || n(p);
  }, [n, p]), /* @__PURE__ */ v.jsxs("div", { className: ["structured-input-editor", o].filter(Boolean).join(" "), children: [
    i ? /* @__PURE__ */ v.jsx("div", { className: "structured-input-description", children: i }) : null,
    /* @__PURE__ */ v.jsx("div", { className: "inline-actions compact-actions", children: /* @__PURE__ */ v.jsx(
      "button",
      {
        type: "button",
        onClick: () => w((y) => [...y, kc()]),
        disabled: l,
        children: s
      }
    ) }),
    C ? /* @__PURE__ */ v.jsx("div", { className: "callout error-callout", children: C }) : null,
    d.length === 0 ? Bi(u, `No ${r.toLowerCase()} rows are configured.`) : null,
    d.map((y, _) => /* @__PURE__ */ v.jsxs("div", { className: "string-list-selector-row structured-input-row structured-input-map-row", children: [
      /* @__PURE__ */ v.jsxs("label", { className: "field-stack structured-input-field", children: [
        /* @__PURE__ */ v.jsx("span", { children: c }),
        /* @__PURE__ */ v.jsx(
          "input",
          {
            value: y.key,
            onChange: (N) => {
              const x = d.map(
                (g, E) => E === _ ? { ...g, key: N.target.value } : g
              );
              w(x), a(x);
            },
            disabled: l
          }
        )
      ] }),
      /* @__PURE__ */ v.jsxs("label", { className: "field-stack structured-input-field", children: [
        /* @__PURE__ */ v.jsx("span", { children: m }),
        /* @__PURE__ */ v.jsx(
          "input",
          {
            value: y.value,
            onChange: (N) => {
              const x = d.map(
                (g, E) => E === _ ? { ...g, value: N.target.value } : g
              );
              w(x), a(x);
            },
            disabled: l
          }
        )
      ] }),
      /* @__PURE__ */ v.jsx("div", { className: "inline-actions compact-actions", children: /* @__PURE__ */ v.jsx(
        "button",
        {
          type: "button",
          className: "danger",
          onClick: () => {
            const N = d.filter((x, g) => g !== _);
            w(N), a(N);
          },
          disabled: l,
          children: "Remove"
        }
      ) })
    ] }, y.id))
  ] });
}
const np = "";
class rp extends Error {
  constructor(n, r, l) {
    super(n);
    Cl(this, "status");
    Cl(this, "payload");
    this.name = "ApiError", this.status = r, this.payload = l;
  }
}
async function Hi(e, t) {
  const n = await fetch(`${np}${e}`, {
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
    throw new rp(String(l.error), n.status, l);
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
    initialPolicy: lp()
  };
}
function lp() {
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
function Ce(e) {
  return e.trim() || null;
}
function xc(e) {
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
function op(e, t) {
  const n = {
    backend: e.backend,
    session_id: Ce(e.sessionId),
    directory_path: Ce(t)
  }, r = xc(e.ttlDuration);
  r != null && (n.ttl_seconds = r);
  const l = Ce(e.model);
  l && (n.model = l);
  const o = Ce(e.agentProvider);
  o && (n.agent_provider = o);
  const i = Ce(e.modelProviderMode);
  if (i && (n.model_provider_mode = i), e.backend === "libvirt_vm") {
    const h = Ce(e.provider);
    n.vm_name = Ce(e.vmName), h && (n.provider = h), n.vm_user = Ce(e.vmUser) ?? "agent";
    const d = Ce(e.vmImage);
    d && (n.vm_image = d), n.dotnet = e.dotnet, n.nuget = e.nuget, n.mssql = e.mssql, n.mssql_init_root = e.mssql ? Ce(e.mssqlInitRoot) : null, n.playwright = e.playwright;
  } else
    n.target_name = Ce(e.targetName), n.container_image = Ce(e.containerImage);
  const u = e.initialPolicy;
  Object.keys(u.labels).length > 0 && (n.labels = u.labels), u.webSearchEnabled && (n.web_search_enabled = !0);
  const s = bl(u.allowedTools);
  s.length > 0 && (n.allowed_tools = s);
  const c = bl(u.allowedScriptNames);
  c.length > 0 && (n.allowed_script_names = c);
  const m = bl(u.communicationTargetIds);
  return m.length > 0 && (n.communication_target_ids = m), n;
}
function ip(e) {
  return e.vmName.trim() || e.targetName.trim() || e.sessionId.trim() || "agent";
}
async function up(e) {
  const t = new URLSearchParams(), n = e.trim();
  n && t.set("directory_path", n);
  const r = t.toString();
  return Hi(
    `/api/admin/agents/create-options${r ? `?${r}` : ""}`
  );
}
async function sp(e) {
  return (await Hi(
    "/api/admin/agents/create-operations",
    {
      method: "POST",
      body: JSON.stringify(e)
    }
  )).operation;
}
async function ap(e) {
  return (await Hi(
    `/api/admin/agents/create-operations/${encodeURIComponent(e)}`
  )).operation;
}
function cp(e) {
  return e.status === "queued" || e.status === "running";
}
function fp(e) {
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
function $({ children: e, tooltip: t }) {
  const n = typeof e == "string" ? e : "Field";
  return /* @__PURE__ */ v.jsxs("span", { className: "config-field-label", children: [
    /* @__PURE__ */ v.jsx("span", { children: e }),
    /* @__PURE__ */ v.jsx(
      Jd,
      {
        content: t,
        focusable: !0,
        ariaLabel: `${n} help`,
        triggerClassName: "config-field-tooltip-trigger",
        children: /* @__PURE__ */ v.jsx("span", { className: "config-field-tooltip-icon", "aria-hidden": "true", children: "i" })
      }
    )
  ] });
}
function dp(e, t, n) {
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
function pp(e) {
  const t = e.trim();
  return !t || xc(t) == null;
}
function mp({
  directoryPath: e,
  onClose: t,
  onCreate: n,
  title: r = "Create agent",
  description: l = null,
  submitLabel: o = "Create",
  submittingLabel: i = "Queueing...",
  closeLabel: u = "Close",
  privilegedUnlocked: s = !0,
  closeOnSuccess: c = !0
}) {
  const [m, h] = D.useState(() => ls()), [d, w] = D.useState(null), [C, S] = D.useState(""), [z, f] = D.useState(null), [a, p] = D.useState(!1);
  D.useEffect(() => {
    let g = !1;
    return w(null), h((L) => ({
      ...L,
      agentProvider: "",
      vmImage: "",
      containerImage: "",
      initialPolicy: {
        ...L.initialPolicy,
        communicationTargetIds: []
      }
    })), (async () => {
      try {
        const L = await up(e);
        g || w(L);
      } catch {
        g || w(null);
      }
    })(), () => {
      g = !0;
    };
  }, [e]);
  const y = D.useMemo(
    () => ((d == null ? void 0 : d.agent_providers) ?? []).filter(
      (g) => g.execution_backends.includes(m.backend)
    ),
    [m.backend, d]
  ), _ = !m.agentProvider || m.agentProvider === "codex", N = (d == null ? void 0 : d.communication_targets) ?? [];
  async function x() {
    if (m.backend === "libvirt_vm" && !m.vmName.trim()) {
      S("VM name is required.");
      return;
    }
    if (m.backend === "container_worker_vm" && !m.sessionId.trim()) {
      S("Session id is required for container workers.");
      return;
    }
    if (pp(m.ttlDuration)) {
      S(
        "Session TTL must use seconds, minutes, hours, days, or weeks, for example 30m, 8h, 7d, or 2w."
      );
      return;
    }
    if (z) {
      S(z);
      return;
    }
    const g = m, E = op(g, e), L = ip(g);
    p(!0), S("");
    try {
      await n(E, { form: g, targetLabel: L }), h(ls()), f(null), c && t();
    } catch (W) {
      S(W instanceof Error ? W.message : String(W));
    } finally {
      p(!1);
    }
  }
  return /* @__PURE__ */ v.jsx("div", { className: "modal-backdrop", role: "presentation", children: /* @__PURE__ */ v.jsxs("section", { className: "modal-card agent-crud-modal", role: "dialog", "aria-modal": "true", "aria-label": r, children: [
    /* @__PURE__ */ v.jsxs("div", { className: "modal-header", children: [
      /* @__PURE__ */ v.jsxs("div", { children: [
        /* @__PURE__ */ v.jsx("h2", { children: r }),
        l ? /* @__PURE__ */ v.jsx("p", { children: l }) : null
      ] }),
      /* @__PURE__ */ v.jsx("button", { className: "admin-action-button compact", type: "button", onClick: t, disabled: a, children: u })
    ] }),
    /* @__PURE__ */ v.jsxs("div", { className: "modal-body", children: [
      C ? /* @__PURE__ */ v.jsx("div", { className: "callout error-callout", children: C }) : null,
      /* @__PURE__ */ v.jsxs("div", { className: "config-field-grid", children: [
        /* @__PURE__ */ v.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ v.jsx($, { tooltip: "Execution target for the agent. VM agent launches a libvirt VM; Container worker prepares a worker session that runs in a managed container.", children: "Backend" }),
          /* @__PURE__ */ v.jsx("span", { className: "select-field-control", children: /* @__PURE__ */ v.jsxs(
            "select",
            {
              value: m.backend,
              onChange: (g) => {
                const E = g.target.value;
                h((L) => dp(L, E, d));
              },
              children: [
                /* @__PURE__ */ v.jsx("option", { value: "libvirt_vm", children: "VM agent" }),
                /* @__PURE__ */ v.jsx("option", { value: "container_worker_vm", children: "Container worker" })
              ]
            }
          ) })
        ] }),
        /* @__PURE__ */ v.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ v.jsx($, { tooltip: "Agent CLI to bootstrap and run in the target. Claude bootstraps Claude Code for VM agents; Directory default uses the configured provider default.", children: "Agent provider" }),
          /* @__PURE__ */ v.jsx("span", { className: "select-field-control", children: /* @__PURE__ */ v.jsxs(
            "select",
            {
              value: m.agentProvider,
              onChange: (g) => h((E) => {
                const L = g.target.value;
                return {
                  ...E,
                  agentProvider: L,
                  modelProviderMode: L && L !== "codex" ? "" : E.modelProviderMode
                };
              }),
              children: [
                /* @__PURE__ */ v.jsx("option", { value: "", children: "Directory default" }),
                y.map((g) => /* @__PURE__ */ v.jsx("option", { value: g.value, children: g.label }, g.value))
              ]
            }
          ) })
        ] }),
        _ ? /* @__PURE__ */ v.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ v.jsx($, { tooltip: "Relay mode for Codex model requests. Leave blank to use the directory default; choose Local OpenAI for host-local Ollama.", children: "Model relay" }),
          /* @__PURE__ */ v.jsx("span", { className: "select-field-control", children: /* @__PURE__ */ v.jsxs(
            "select",
            {
              value: m.modelProviderMode,
              onChange: (g) => h((E) => ({
                ...E,
                modelProviderMode: g.target.value
              })),
              children: [
                /* @__PURE__ */ v.jsx("option", { value: "", children: "Directory default" }),
                ((d == null ? void 0 : d.model_provider_modes) ?? []).map((g) => /* @__PURE__ */ v.jsx("option", { value: g.value, children: g.label }, g.value))
              ]
            }
          ) })
        ] }) : null,
        /* @__PURE__ */ v.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ v.jsx($, { tooltip: "Host-side session id for policy, logs, model routing, and resume state. Required for container workers; VM agents can derive one from the VM name.", children: "Session" }),
          /* @__PURE__ */ v.jsx(
            "input",
            {
              value: m.sessionId,
              onChange: (g) => h((E) => ({ ...E, sessionId: g.target.value })),
              placeholder: m.backend === "container_worker_vm" ? "Required" : "Optional"
            }
          )
        ] }),
        /* @__PURE__ */ v.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ v.jsx($, { tooltip: "How long the created session remains active before expiry. Use s, m, h, d, or w, for example 30m, 8h, 7d, or 2w.", children: "Session TTL" }),
          /* @__PURE__ */ v.jsx(
            "input",
            {
              value: m.ttlDuration,
              onChange: (g) => h((E) => ({ ...E, ttlDuration: g.target.value })),
              placeholder: "7d"
            }
          )
        ] }),
        /* @__PURE__ */ v.jsxs("label", { className: "field-stack", children: [
          /* @__PURE__ */ v.jsx($, { tooltip: "Model stored on the session for runtime prompts and resumes. Leave blank to use the selected agent provider or directory default.", children: "Model" }),
          /* @__PURE__ */ v.jsx(
            "input",
            {
              value: m.model,
              onChange: (g) => h((E) => ({ ...E, model: g.target.value })),
              placeholder: m.agentProvider === "claude" ? "claude-sonnet-4-6" : "Directory default"
            }
          )
        ] }),
        m.backend === "libvirt_vm" ? /* @__PURE__ */ v.jsxs(v.Fragment, { children: [
          /* @__PURE__ */ v.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ v.jsx($, { tooltip: "Libvirt domain name to create for this VM-backed agent. It must be unique on the host.", children: "VM name" }),
            /* @__PURE__ */ v.jsx(
              "input",
              {
                value: m.vmName,
                onChange: (g) => h((E) => ({ ...E, vmName: g.target.value }))
              }
            )
          ] }),
          /* @__PURE__ */ v.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ v.jsx($, { tooltip: "VM infrastructure provider, not the model provider. Leave blank to use the directory default, usually libvirt.", children: "Provider" }),
            /* @__PURE__ */ v.jsx(
              "input",
              {
                value: m.provider,
                onChange: (g) => h((E) => ({ ...E, provider: g.target.value })),
                placeholder: "Directory default"
              }
            )
          ] }),
          /* @__PURE__ */ v.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ v.jsx($, { tooltip: "Guest runtime user used for bootstraps and agent commands. Current standard VM images use agent.", children: "VM user" }),
            /* @__PURE__ */ v.jsx(
              "input",
              {
                value: m.vmUser,
                onChange: (g) => h((E) => ({ ...E, vmUser: g.target.value }))
              }
            )
          ] }),
          /* @__PURE__ */ v.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ v.jsx($, { tooltip: "Approved libvirt template alias for the base qcow2 image. Leave blank to use the directory default.", children: "VM image" }),
            /* @__PURE__ */ v.jsx("span", { className: "select-field-control", children: /* @__PURE__ */ v.jsxs(
              "select",
              {
                value: m.vmImage,
                onChange: (g) => h((E) => ({ ...E, vmImage: g.target.value })),
                children: [
                  /* @__PURE__ */ v.jsx("option", { value: "", children: `Directory default (${(d == null ? void 0 : d.libvirt.default_template) ?? "default"})` }),
                  ((d == null ? void 0 : d.libvirt.templates) ?? []).map((g) => /* @__PURE__ */ v.jsx("option", { value: g.alias, children: g.alias }, g.alias))
                ]
              }
            ) })
          ] })
        ] }) : /* @__PURE__ */ v.jsxs(v.Fragment, { children: [
          /* @__PURE__ */ v.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ v.jsx($, { tooltip: "Display and control-plane target name for this container-worker agent. Leave blank to reuse the session id.", children: "Target name" }),
            /* @__PURE__ */ v.jsx(
              "input",
              {
                value: m.targetName,
                onChange: (g) => h((E) => ({ ...E, targetName: g.target.value })),
                placeholder: "Optional"
              }
            )
          ] }),
          /* @__PURE__ */ v.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ v.jsx($, { tooltip: "Approved container image alias for worker sessions. Leave blank to use the directory default runner image.", children: "Container image selector" }),
            d ? /* @__PURE__ */ v.jsx("span", { className: "select-field-control", children: /* @__PURE__ */ v.jsxs(
              "select",
              {
                value: m.containerImage,
                onChange: (g) => h((E) => ({ ...E, containerImage: g.target.value })),
                children: [
                  /* @__PURE__ */ v.jsx("option", { value: "", children: `Default image (${d.container_worker.default_image ?? "default"})` }),
                  d.container_worker.images.map((g) => /* @__PURE__ */ v.jsx("option", { value: g.alias, children: g.alias }, g.alias))
                ]
              }
            ) }) : /* @__PURE__ */ v.jsx(
              "input",
              {
                value: m.containerImage,
                onChange: (g) => h((E) => ({ ...E, containerImage: g.target.value })),
                placeholder: "Alias or default image"
              }
            )
          ] })
        ] }),
        m.backend === "libvirt_vm" ? /* @__PURE__ */ v.jsxs(v.Fragment, { children: [
          /* @__PURE__ */ v.jsxs("label", { className: "checkbox-row agent-checkbox-row", children: [
            /* @__PURE__ */ v.jsx(
              "input",
              {
                type: "checkbox",
                checked: m.dotnet,
                onChange: (g) => h((E) => ({ ...E, dotnet: g.target.checked }))
              }
            ),
            /* @__PURE__ */ v.jsx($, { tooltip: "Copy the host .NET SDK into the VM after launch.", children: ".NET" })
          ] }),
          /* @__PURE__ */ v.jsxs("label", { className: "checkbox-row agent-checkbox-row", children: [
            /* @__PURE__ */ v.jsx(
              "input",
              {
                type: "checkbox",
                checked: m.nuget,
                onChange: (g) => h((E) => ({ ...E, nuget: g.target.checked }))
              }
            ),
            /* @__PURE__ */ v.jsx($, { tooltip: "Configure the guest to restore packages through the host NuGet relay and cache.", children: "NuGet" })
          ] }),
          /* @__PURE__ */ v.jsxs("label", { className: "checkbox-row agent-checkbox-row", children: [
            /* @__PURE__ */ v.jsx(
              "input",
              {
                type: "checkbox",
                checked: m.mssql,
                onChange: (g) => h((E) => ({ ...E, mssql: g.target.checked }))
              }
            ),
            /* @__PURE__ */ v.jsx($, { tooltip: "Provision a guest-local SQL Server instance. Requires MSSQL_SA_PASSWORD on the host.", children: "MSSQL" })
          ] }),
          /* @__PURE__ */ v.jsxs("label", { className: "checkbox-row agent-checkbox-row", children: [
            /* @__PURE__ */ v.jsx(
              "input",
              {
                type: "checkbox",
                checked: m.playwright,
                onChange: (g) => h((E) => ({ ...E, playwright: g.target.checked }))
              }
            ),
            /* @__PURE__ */ v.jsx($, { tooltip: "Install host-seeded Node and Playwright browser dependencies in the VM.", children: "Playwright" })
          ] }),
          /* @__PURE__ */ v.jsxs("label", { className: "field-stack", children: [
            /* @__PURE__ */ v.jsx($, { tooltip: "Optional host directory copied into the SQL init folder. If init.sql exists there, it runs after SQL Server starts.", children: "MSSQL init root" }),
            /* @__PURE__ */ v.jsx(
              "input",
              {
                value: m.mssqlInitRoot,
                onChange: (g) => h((E) => ({ ...E, mssqlInitRoot: g.target.value })),
                disabled: !m.mssql
              }
            )
          ] })
        ] }) : null,
        /* @__PURE__ */ v.jsxs("div", { className: "field-stack wide-field", children: [
          /* @__PURE__ */ v.jsx($, { tooltip: "Session policy values written when the agent is created. Empty rows are ignored.", children: "Initial policy" }),
          /* @__PURE__ */ v.jsx("p", { className: "field-description", children: "Optional session policy values to apply when the agent is created. Blank rows are not sent." })
        ] }),
        /* @__PURE__ */ v.jsxs("div", { className: "field-stack", children: [
          /* @__PURE__ */ v.jsx($, { tooltip: "Key/value metadata stored on the session and shown in agent lists.", children: "Labels" }),
          /* @__PURE__ */ v.jsx(
            tp,
            {
              value: m.initialPolicy.labels,
              onChange: (g) => h((E) => ({
                ...E,
                initialPolicy: { ...E.initialPolicy, labels: g }
              })),
              onValidationChange: f,
              fieldLabel: "Labels",
              emptyState: "No labels are configured.",
              addLabel: "Add label",
              keyLabel: "Key",
              valueLabel: "Value"
            }
          )
        ] }),
        /* @__PURE__ */ v.jsxs("div", { className: "field-stack", children: [
          /* @__PURE__ */ v.jsx($, { tooltip: "MCP tools this session may call without being hidden by policy. Approval rules still apply.", children: "Allowed tools" }),
          /* @__PURE__ */ v.jsx(
            rs,
            {
              values: m.initialPolicy.allowedTools,
              onChange: (g) => h((E) => ({
                ...E,
                initialPolicy: { ...E.initialPolicy, allowedTools: g }
              })),
              addLabel: "Add tool",
              itemLabel: "Tool",
              placeholder: "run_script",
              emptyState: "No tools are allowed explicitly."
            }
          )
        ] }),
        /* @__PURE__ */ v.jsxs("label", { className: "checkbox-row agent-checkbox-row", children: [
          /* @__PURE__ */ v.jsx(
            "input",
            {
              type: "checkbox",
              checked: m.initialPolicy.webSearchEnabled,
              onChange: (g) => h((E) => ({
                ...E,
                initialPolicy: { ...E.initialPolicy, webSearchEnabled: g.target.checked }
              }))
            }
          ),
          /* @__PURE__ */ v.jsx($, { tooltip: "Allow this session's agent runner to use live web search when the selected provider supports it.", children: "Web search" })
        ] }),
        /* @__PURE__ */ v.jsxs("div", { className: "field-stack", children: [
          /* @__PURE__ */ v.jsx($, { tooltip: "Script names this session may run through approved script execution paths.", children: "Allowed scripts" }),
          /* @__PURE__ */ v.jsx(
            rs,
            {
              values: m.initialPolicy.allowedScriptNames,
              onChange: (g) => h((E) => ({
                ...E,
                initialPolicy: { ...E.initialPolicy, allowedScriptNames: g }
              })),
              addLabel: "Add script",
              itemLabel: "Script",
              placeholder: "collect-logs",
              emptyState: "No scripts are allowed explicitly."
            }
          )
        ] }),
        /* @__PURE__ */ v.jsxs("div", { className: "field-stack", children: [
          /* @__PURE__ */ v.jsx($, { tooltip: "Directory communication targets the session may use for messaging. Only registered targets with session-compatible capabilities are selectable.", children: "Communication targets" }),
          /* @__PURE__ */ v.jsx(
            ep,
            {
              values: m.initialPolicy.communicationTargetIds,
              options: N,
              onChange: (g) => h((E) => ({
                ...E,
                initialPolicy: { ...E.initialPolicy, communicationTargetIds: g }
              })),
              emptyState: N.length ? "No communication targets selected." : "No session-capable communication targets are registered for this directory."
            }
          )
        ] })
      ] }),
      /* @__PURE__ */ v.jsx("div", { className: "inline-actions compact-actions", children: /* @__PURE__ */ v.jsx("button", { className: "admin-action-button primary compact", type: "button", disabled: !s || a, onClick: () => void x(), children: a ? i : o }) })
    ] })
  ] }) });
}
const hp = 1500, vp = 10 * 60 * 1e3;
function gp(e) {
  return new Promise((t) => window.setTimeout(t, e));
}
async function yp(e) {
  let t = e;
  const n = Date.now() + vp;
  for (; cp(t); ) {
    if (Date.now() >= n)
      throw new Error(`Timed out waiting for ${t.target_name} to finish creating.`);
    await gp(hp), t = await ap(t.operation_id);
  }
  if (t.status === "failed")
    throw new Error(t.error || `Agent create failed for ${t.target_name}.`);
  return t;
}
function wp(e) {
  window.dispatchEvent(new CustomEvent("team-agent-create:created", { detail: e }));
}
function kp() {
  var o;
  const [e, t] = D.useState(null), [n, r] = D.useState("");
  if (D.useEffect(() => {
    const i = (s) => {
      r(""), t({
        contextId: (s == null ? void 0 : s.contextId) ?? null,
        directoryPath: (s == null ? void 0 : s.directoryPath) ?? "root"
      });
    };
    window.openTeamAgentCreateDialog = i;
    const u = (s) => {
      i(s.detail ?? {});
    };
    return window.addEventListener("team-agent-create:open", u), () => {
      window.openTeamAgentCreateDialog === i && delete window.openTeamAgentCreateDialog, window.removeEventListener("team-agent-create:open", u);
    };
  }, []), !e)
    return null;
  async function l(i) {
    r("Queueing core agent session.");
    const u = await sp(i);
    r(`Creating ${u.target_name}.`);
    const s = await yp(u), c = fp(s);
    if (!c)
      throw new Error(`Created ${s.target_name}, but the create operation did not return a session id.`);
    r("Registering Team agent."), wp({
      contextId: (e == null ? void 0 : e.contextId) ?? null,
      sessionId: c,
      targetName: s.target_name,
      operation: s
    });
  }
  return /* @__PURE__ */ v.jsx(
    mp,
    {
      directoryPath: ((o = e.directoryPath) == null ? void 0 : o.trim()) || "root",
      title: "Create and register agent",
      description: n || "Create a VM or container session, then Team registration will continue automatically.",
      submitLabel: "Create and register",
      submittingLabel: "Creating...",
      privilegedUnlocked: !0,
      closeOnSuccess: !0,
      onClose: () => {
        t(null), r("");
      },
      onCreate: l
    }
  );
}
function Sp() {
  let e = document.querySelector("#team-agent-create-root");
  return e || (e = document.createElement("div"), e.id = "team-agent-create-root", document.body.appendChild(e)), e;
}
let eo = null;
function os() {
  const e = Sp();
  eo || (eo = wc(e)), eo.render(/* @__PURE__ */ v.jsx(kp, {}));
}
document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", os, { once: !0 }) : os();
