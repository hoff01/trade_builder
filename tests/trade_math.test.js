'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const TradeMath = require('../app/static/trade_math.js');

test('rounding is deterministic and capped at five decimals', () => {
    assert.equal(TradeMath.round5(1.23456789), 1.23457);
    assert.equal(TradeMath.round(-1.234565, 5), -1.23457);
    assert.equal(TradeMath.round(12.3456789, 9), 12.34568);
    assert.equal(TradeMath.round5(-0.0000001), 0);
    assert.equal(TradeMath.round5(Number.NaN), null);
});

test('converts cpg, dollars per gallon, dollars per barrel, and dollars per MT', () => {
    const factors = { bbl_per_mt: 7.33, gal_per_bbl: 42 };
    assert.equal(TradeMath.convertValue(100, 'cpg', '$/gal', factors), 1);
    assert.equal(TradeMath.convertValue(100, 'cpg', '$/bbl', factors), 42);
    assert.equal(TradeMath.convertValue(42, '$/bbl', 'cpg', factors), 100);
    assert.equal(TradeMath.convertValue(733, '$/MT', '$/bbl', factors), 100);
    assert.equal(TradeMath.convertValue(1, '$/gal', '$/MT', factors), 307.86);
});

test('converts every mixed-unit leg before applying weights', () => {
    const result = TradeMath.weightedSum([
        { value: 100, native_unit: 'cpg', ratio: 1 },
        { value: 42, native_unit: '$/bbl', ratio: -0.5 },
        { value: 7.33, native_unit: '$/MT', ratio: 1 }
    ], '$/bbl', { bbl_per_mt: 7.33, gal_per_bbl: 42 });
    assert.equal(result, 22);
});

test('interpolates a holiday mismatch only between two nearby observations', () => {
    const aligned = TradeMath.interpolateSeriesAtTargets(
        { x: [1, 3], y: [10, 14] },
        [0, 1, 2, 3, 4]
    );
    assert.deepEqual(aligned, {
        x: [0, 1, 2, 3, 4],
        y: [null, 10, 12, 14, null],
        interpolated: [false, false, true, false, false]
    });
});

test('combines holiday-mismatched legs without filling long or unbounded gaps', () => {
    const combined = TradeMath.combineWeightedSeries([
        { series: { x: [1, 3, 10], y: [10, 14, 30] }, ratio: 1, native_unit: '$/bbl' },
        { series: { x: [1, 2, 3, 5, 10], y: [3, 4, 5, 6, 7] }, ratio: -1, native_unit: '$/bbl' }
    ], '$/bbl', { maxSpanDays: 4 });

    assert.deepEqual(combined, {
        x: [1, 2, 3, 10],
        y: [7, 8, 9, 23],
        interpolatedPoints: 1
    });
});

test('builds Bloomberg-style WU and HO tickers with yellow keys', () => {
    const config = {
        yellow_key: 'Comdty',
        ticker_template: '{root}{month_code}{year_2d} {yellow_key}'
    };
    assert.equal(TradeMath.buildTicker('WU', 'Jan', 2026, config), 'WUF26 Comdty');
    assert.equal(TradeMath.buildTicker('HO', 'F', 2026, config), 'HOF26 Comdty');
    assert.equal(TradeMath.buildTicker('HO', 'Feb', 2026, {
        yellow_key: 'Comdty',
        ticker_template: '{root}{month_code}{y} {yellow_key}'
    }), 'HOG6 Comdty');
    assert.equal(TradeMath.buildTicker('WU', 'Sep', 2026, {
        yellow_key: 'Index',
        ticker_template: '{root}{month_code}{year_1d} {yellow_key}'
    }), 'WUU6 Index');
});

test('parses WUF26 and HOF26 contracts instead of matching a literal backslash-d', () => {
    assert.deepEqual(TradeMath.parseTicker('WUF26'), {
        raw: 'WUF26', root: 'WU', monthCode: 'F', month: 'Jan', year: 2026, yearDigits: '26', yellowKey: ''
    });
    assert.deepEqual(TradeMath.parseTicker('HOF26 Comdty'), {
        raw: 'HOF26 Comdty', root: 'HO', monthCode: 'F', month: 'Jan', year: 2026, yearDigits: '26', yellowKey: 'Comdty'
    });
});

test('honors a custom ticker template', () => {
    const ticker = TradeMath.buildTicker('WU', 'Dec', 2027, {
        yellow_key: 'Index',
        ticker_template: '{root}-{month_code}-{yyyy} {yellow_key}'
    });
    assert.equal(ticker, 'WU-Z-2027 Index');
});

test('normalizes RVO as a flat monthless curve', () => {
    assert.equal(TradeMath.normalizeCurveMode('Flat'), 'flat');
    assert.equal(TradeMath.normalizeCurveMode('flat forward'), 'flat');
    assert.equal(TradeMath.normalizeCurveMode('Monthly'), 'monthly');
    assert.equal(TradeMath.normalizeCurveMode(''), 'monthly');
});

test('aligns each daily flat value to the selected forward-curve cycle', () => {
    const aligned = TradeMath.alignFlatCurveSeries({
        2025: { x: [1, 31, 32, 366], y: [10, 11, 12, 13] },
        2026: { x: [1, 31, 32], y: [20, 21, 22] }
    }, [2026], 2);

    assert.deepEqual(aligned, {
        2026: { x: [32, 366, 367, 397], y: [12, 13, 20, 21] }
    });
    assert.deepEqual(
        TradeMath.alignFlatCurveSeries({ 2025: { x: [1, 366], y: [10, 13] } }, [2026], 1),
        { 2026: { x: [1, 366], y: [10, 13] } }
    );
});
