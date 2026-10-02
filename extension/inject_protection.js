// Flow2API Protection against Google Flow anti-extension honeypot (extension_hijack_detected)
(() => {
    'use strict';
    if (window.__flow2api_protected) return;
    window.__flow2api_protected = true;

    // 0. Hook Object.assign to neutralize extension_hijack_detected wrapper anywhere it is called
    try {
        const origAssign = Object.assign;
        if (!Object.__flow2api_assign_hooked) {
            Object.assign = function(target, ...sources) {
                const cleaned = sources.map(src => {
                    if (src && typeof src === 'object' && src.action === 'extension_hijack_detected') {
                        const copy = origAssign({}, src);
                        delete copy.action;
                        return copy;
                    }
                    return src;
                });
                return origAssign.apply(Object, [target, ...cleaned]);
            };
            Object.__flow2api_assign_hooked = true;
        }
    } catch (e) {}

    // 1. Defend cG.prototype.Aa (enable_recaptcha_execute_closure_wrap) and ma (enable_prompt_submit_is_trusted_check)
    function patchCG(cgClass) {
        if (!cgClass || !cgClass.prototype) return;
        try {
            Object.defineProperty(cgClass.prototype, 'Aa', {
                get: () => false,
                set: () => {},
                configurable: true,
                enumerable: true
            });
        } catch (e) {}
        try {
            Object.defineProperty(cgClass.prototype, 'ma', {
                get: () => false,
                set: () => {},
                configurable: true,
                enumerable: true
            });
        } catch (e) {}
    }

    function hookMod(mod) {
        if (!mod || typeof mod !== 'object') return;
        let _cg = mod.cG;
        if (_cg) patchCG(_cg);
        try {
            Object.defineProperty(mod, 'cG', {
                configurable: true,
                enumerable: true,
                get: () => _cg,
                set: (cgVal) => {
                    _cg = cgVal;
                    patchCG(cgVal);
                }
            });
        } catch (e) {
            patchCG(mod.cG);
        }
    }

    let _frontend = window.default_AiSandboxAngularFrontend;
    if (_frontend) {
        hookMod(_frontend);
    }
    try {
        Object.defineProperty(window, 'default_AiSandboxAngularFrontend', {
            configurable: true,
            enumerable: true,
            get: () => _frontend,
            set: (val) => {
                _frontend = val;
                hookMod(val);
            }
        });
    } catch (e) {}

    // 2. Defend TSDtV experiment flags in WIZ_global_data
    function patchWizData(wiz) {
        if (!wiz || typeof wiz !== 'object') return;
        try {
            if (typeof wiz.TSDtV === 'string' && wiz.TSDtV.includes('45846838')) {
                wiz.TSDtV = wiz.TSDtV.replace(/\[45846838,null,true,/g, '[45846838,null,false,');
            }
            if (typeof wiz.TSDtV === 'string' && wiz.TSDtV.includes('45843730')) {
                wiz.TSDtV = wiz.TSDtV.replace(/\[45843730,null,true,/g, '[45843730,null,false,');
            }
        } catch (e) {}
    }

    let _wiz = window.WIZ_global_data;
    if (_wiz) {
        patchWizData(_wiz);
    }
    try {
        Object.defineProperty(window, 'WIZ_global_data', {
            configurable: true,
            enumerable: true,
            get: () => _wiz,
            set: (val) => {
                _wiz = val;
                patchWizData(val);
            }
        });
    } catch (e) {}

    // 3. Defend grecaptcha.enterprise.execute from being overwritten with extension_hijack_detected
    let _realExecute = null;
    function protectEnterprise(enterprise) {
        if (!enterprise || enterprise.__flow2api_hooked) return;
        enterprise.__flow2api_hooked = true;

        let _currentExecute = enterprise.execute;
        if (typeof _currentExecute === 'function' && !_currentExecute.toString().includes('extension_hijack')) {
            _realExecute = _currentExecute;
        }

        try {
            Object.defineProperty(enterprise, 'execute', {
                configurable: true,
                enumerable: true,
                get: () => {
                    return _realExecute || _currentExecute;
                },
                set: (fn) => {
                    if (typeof fn === 'function') {
                        if (fn.toString().includes('extension_hijack')) {
                            console.warn('[Flow2API] Intercepted and blocked extension_hijack_detected wrapper overwrite');
                            return;
                        }
                        _realExecute = fn;
                        _currentExecute = fn;
                    }
                }
            });
        } catch (e) {}
    }

    let _grecaptcha = window.grecaptcha;
    if (_grecaptcha && _grecaptcha.enterprise) {
        protectEnterprise(_grecaptcha.enterprise);
    }

    try {
        Object.defineProperty(window, 'grecaptcha', {
            configurable: true,
            enumerable: true,
            get: () => _grecaptcha,
            set: (val) => {
                _grecaptcha = val;
                if (val && typeof val === 'object') {
                    if (val.enterprise) {
                        protectEnterprise(val.enterprise);
                    } else {
                        let _ent = val.enterprise;
                        try {
                            Object.defineProperty(val, 'enterprise', {
                                configurable: true,
                                enumerable: true,
                                get: () => _ent,
                                set: (entVal) => {
                                    _ent = entVal;
                                    protectEnterprise(entVal);
                                }
                            });
                        } catch (e) {
                            protectEnterprise(val.enterprise);
                        }
                    }
                }
            }
        });
    } catch (e) {}
})();
