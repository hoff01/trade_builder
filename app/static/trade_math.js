(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    root.TradeMath = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const MAX_DECIMALS = 5;
    const DEFAULT_BBL_PER_MT = 7.33;
    const DEFAULT_GAL_PER_BBL = 42;
    const MONTH_CODES = Object.freeze({
        JAN: 'F', FEB: 'G', MAR: 'H', APR: 'J', MAY: 'K', JUN: 'M',
        JUL: 'N', AUG: 'Q', SEP: 'U', OCT: 'V', NOV: 'X', DEC: 'Z'
    });
    const MONTH_NAMES = Object.freeze(Object.entries(MONTH_CODES).reduce((acc, entry) => {
        acc[entry[1]] = entry[0][0] + entry[0].slice(1).toLowerCase();
        return acc;
    }, {}));
    const CONTRACT_MONTH_PATTERN = '[FGHJKMNQUVXZ]';
    const MONTH_AXIS_BOUNDARIES = Object.freeze([1, 32, 61, 92, 122, 153, 183, 214, 245, 275, 306, 336, 367]);

    function asFinite(value) {
        if (value === '' || value == null) return null;
        const number = Number(value);
        return Number.isFinite(number) ? number : null;
    }

    function round(value, decimals) {
        const number = asFinite(value);
        if (number == null) return null;
        const requested = decimals == null ? MAX_DECIMALS : Math.trunc(Number(decimals));
        const places = Number.isFinite(requested)
            ? Math.max(0, Math.min(MAX_DECIMALS, requested))
            : MAX_DECIMALS;
        const factor = 10 ** places;
        const sign = number < 0 ? -1 : 1;
        const result = sign * (Math.round((Math.abs(number) + Number.EPSILON) * factor) / factor);
        return Object.is(result, -0) ? 0 : result;
    }

    function round5(value) {
        return round(value, MAX_DECIMALS);
    }

    function format(value, decimals) {
        const rounded = round(value, decimals);
        return rounded == null ? '' : String(rounded);
    }

    function normalizeUnit(unit) {
        const value = String(unit == null ? '' : unit)
            .trim()
            .replace(/\s+/g, '')
            .toLowerCase();
        if (value === 'cpg' || value === 'cents/gal' || value === 'cents/gallon') return 'cpg';
        if (value === '$/gal' || value === 'usd/gal' || value === '$/gallon' || value === 'usd/gallon') return '$/gal';
        if (value === '$/bbl' || value === 'usd/bbl' || value === '$/barrel' || value === 'usd/barrel') return '$/bbl';
        if (value === '$/mt' || value === 'usd/mt' || value === '$/metricton' || value === 'usd/metricton') return '$/MT';
        return '';
    }

    function normalizeCurveMode(value) {
        const mode = String(value == null ? '' : value)
            .trim()
            .replace(/[\s_-]+/g, '')
            .toLowerCase();
        if (mode === 'flat' || mode === 'flatforward' || mode === 'monthless' || mode === 'spot') {
            return 'flat';
        }
        return 'monthly';
    }

    function alignFlatCurveSeries(seriesMap, targetYears, rollMonthIndex) {
        const source = seriesMap && typeof seriesMap === 'object' ? seriesMap : {};
        const monthIndex = Math.trunc(Number(rollMonthIndex));
        if (monthIndex < 1 || monthIndex > 12 || !Array.isArray(targetYears)) return {};
        const boundary = MONTH_AXIS_BOUNDARIES[monthIndex - 1];
        const aligned = {};

        function appendWindow(output, series, predicate, offset) {
            if (!series || !Array.isArray(series.x) || !Array.isArray(series.y)) return;
            const length = Math.min(series.x.length, series.y.length);
            for (let index = 0; index < length; index++) {
                const x = Number(series.x[index]);
                if (!Number.isFinite(x) || !predicate(x)) continue;
                output.push({ x: x + offset, y: series.y[index] });
            }
        }

        targetYears.forEach((targetYearValue) => {
            const targetYear = Number(targetYearValue);
            if (!Number.isFinite(targetYear)) return;
            const points = [];
            appendWindow(points, source[String(targetYear - 1)] || source[targetYear - 1], (x) => x >= boundary, 0);
            if (monthIndex > 1) {
                appendWindow(points, source[String(targetYear)] || source[targetYear], (x) => x < boundary, 366);
            }
            if (!points.length) return;
            points.sort((a, b) => a.x - b.x);
            aligned[String(targetYear)] = {
                x: points.map((point) => point.x),
                y: points.map((point) => point.y)
            };
        });
        return aligned;
    }

    function normalizeConversionConfig(config) {
        const source = config && typeof config === 'object' ? config : {};
        const bblPerMT = asFinite(source.bblPerMT != null ? source.bblPerMT : source.bbl_per_mt);
        const galPerBbl = asFinite(source.galPerBbl != null ? source.galPerBbl : source.gal_per_bbl);
        return {
            bblPerMT: bblPerMT != null && bblPerMT > 0 ? bblPerMT : DEFAULT_BBL_PER_MT,
            galPerBbl: galPerBbl != null && galPerBbl > 0 ? galPerBbl : DEFAULT_GAL_PER_BBL
        };
    }

    function toDollarsPerGallon(value, fromUnit, config) {
        const unit = normalizeUnit(fromUnit);
        const factors = normalizeConversionConfig(config);
        if (unit === 'cpg') return value / 100;
        if (unit === '$/gal') return value;
        if (unit === '$/bbl') return value / factors.galPerBbl;
        if (unit === '$/MT') return value / (factors.bblPerMT * factors.galPerBbl);
        return null;
    }

    function fromDollarsPerGallon(value, targetUnit, config) {
        const unit = normalizeUnit(targetUnit);
        const factors = normalizeConversionConfig(config);
        if (unit === 'cpg') return value * 100;
        if (unit === '$/gal') return value;
        if (unit === '$/bbl') return value * factors.galPerBbl;
        if (unit === '$/MT') return value * factors.galPerBbl * factors.bblPerMT;
        return null;
    }

    function convertValue(value, fromUnit, targetUnit, config) {
        const number = asFinite(value);
        const from = normalizeUnit(fromUnit);
        const target = normalizeUnit(targetUnit);
        if (number == null || !from || !target) return null;
        if (from === target) return round5(number);
        const dollarsPerGallon = toDollarsPerGallon(number, from, config);
        if (dollarsPerGallon == null) return null;
        return round5(fromDollarsPerGallon(dollarsPerGallon, target, config));
    }

    function conversionFactor(fromUnit, targetUnit, config) {
        return convertValue(1, fromUnit, targetUnit, config);
    }

    function weightedSum(legs, targetUnit, defaultConfig) {
        if (!Array.isArray(legs) || !legs.length) return null;
        let total = 0;
        let used = 0;
        for (const leg of legs) {
            if (!leg) return null;
            const ratio = asFinite(leg.ratio != null ? leg.ratio : leg.weight);
            const value = asFinite(leg.value);
            if (ratio == null || value == null) return null;
            if (ratio === 0) continue;
            const converted = convertValue(
                value,
                leg.nativeUnit || leg.native_unit || leg.unit || targetUnit,
                targetUnit,
                leg.config || defaultConfig
            );
            if (converted == null) return null;
            total += converted * ratio;
            used += 1;
        }
        return used ? round5(total) : 0;
    }

    function monthToCode(month) {
        const value = String(month == null ? '' : month).trim().toUpperCase();
        if (new RegExp(`^${CONTRACT_MONTH_PATTERN}$`).test(value)) return value;
        return MONTH_CODES[value.slice(0, 3)] || '';
    }

    function expandContractYear(value, referenceYear) {
        const text = String(value == null ? '' : value).trim();
        if (!/^\d{1,4}$/.test(text)) return null;
        const number = Number(text);
        if (text.length === 4) return number;
        if (text.length === 2) return number >= 70 ? 1900 + number : 2000 + number;
        if (text.length === 1) {
            const reference = asFinite(referenceYear) || new Date().getFullYear();
            const decade = Math.floor(reference / 10) * 10;
            let candidate = decade + number;
            if (candidate < reference - 5) candidate += 10;
            if (candidate > reference + 5) candidate -= 10;
            return candidate;
        }
        return null;
    }

    function parseTicker(value, options) {
        const settings = options && typeof options === 'object' ? options : {};
        const raw = String(value == null ? '' : value).trim();
        if (!raw) return null;
        let yellowKey = '';
        let contract = raw;
        const configuredKey = String(settings.yellow_key || settings.yellowKey || '').trim();
        if (configuredKey) {
            const escaped = configuredKey.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            const configuredMatch = contract.match(new RegExp(`\\s+(${escaped})$`, 'i'));
            if (configuredMatch) {
                yellowKey = configuredMatch[1];
                contract = contract.slice(0, configuredMatch.index).trim();
            }
        }
        if (!yellowKey) {
            const genericMatch = contract.match(/\s+(Comdty|Index|Equity|Curncy)$/i);
            if (genericMatch) {
                yellowKey = genericMatch[1];
                contract = contract.slice(0, genericMatch.index).trim();
            }
        }
        contract = contract.replace(/[\s_-]+/g, '');
        const rootHint = String(settings.root || settings.root_hint || settings.rootHint || '').trim().toUpperCase();
        let match = null;
        if (rootHint && contract.toUpperCase().startsWith(rootHint)) {
            const tail = contract.slice(rootHint.length);
            const tailMatch = tail.match(new RegExp(`^(${CONTRACT_MONTH_PATTERN})(\\d{1,4})$`, 'i'));
            if (tailMatch) match = [contract, rootHint, tailMatch[1], tailMatch[2]];
        }
        if (!match) {
            match = contract.match(new RegExp(`^(.*?)(${CONTRACT_MONTH_PATTERN})(\\d{1,4})$`, 'i'));
        }
        if (!match || !match[1]) return null;
        const monthCode = match[2].toUpperCase();
        const yearDigits = match[3];
        const year = expandContractYear(yearDigits, settings.reference_year || settings.referenceYear);
        if (!year) return null;
        return {
            raw,
            root: match[1].toUpperCase(),
            monthCode,
            month: MONTH_NAMES[monthCode] || null,
            year,
            yearDigits,
            yellowKey
        };
    }

    function applyTickerTemplate(template, values) {
        return String(template || '')
            .replace(/\{([a-zA-Z0-9_]+)\}/g, function (match, key) {
                return Object.prototype.hasOwnProperty.call(values, key) ? values[key] : match;
            })
            .replace(/\s+/g, ' ')
            .trim();
    }

    function buildTicker(rootCode, month, year, config) {
        const settings = config && typeof config === 'object' ? config : {};
        const rootValue = String(rootCode || settings.root || settings.security_root || '').trim().toUpperCase();
        const monthCode = monthToCode(month);
        const fullYear = expandContractYear(year, settings.reference_year || settings.referenceYear);
        if (!rootValue || !monthCode || !fullYear) return '';
        const yellowKey = String(settings.yellow_key || settings.yellowKey || '').trim();
        const yy = String(fullYear).slice(-2).padStart(2, '0');
        const y = String(fullYear).slice(-1);
        const values = {
            root: rootValue,
            security_root: rootValue,
            month: monthCode,
            month_code: monthCode,
            contract_month_code: monthCode,
            year: String(fullYear),
            yyyy: String(fullYear),
            y,
            year_1d: y,
            year_digit: y,
            yy,
            year2: yy,
            year_2d: yy,
            year_short: yy,
            yellow_key: yellowKey,
            yellowKey
        };
        const template = settings.ticker_template || settings.tickerTemplate || '{root}{month_code}{yy} {yellow_key}';
        return applyTickerTemplate(template, values).replace(/\s+$/, '');
    }

    return Object.freeze({
        MAX_DECIMALS,
        DEFAULT_BBL_PER_MT,
        DEFAULT_GAL_PER_BBL,
        MONTH_CODES,
        MONTH_NAMES,
        asFinite,
        round,
        round5,
        format,
        normalizeUnit,
        normalizeCurveMode,
        alignFlatCurveSeries,
        normalizeConversionConfig,
        convertValue,
        conversionFactor,
        weightedSum,
        monthToCode,
        expandContractYear,
        parseTicker,
        applyTickerTemplate,
        buildTicker
    });
}));
