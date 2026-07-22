%% NTC Thermistor ADC → Temperature Fitting (MATLAB)
% ===================================================
% Fits NTC thermistor ADC readings to temperature using polynomial regression.
% Uses pre-computed NTC resistance-temperature lookup table.
%
% Input:  ntcb.xlsx / ntcb.csv (ADC, TEMP columns)
% Output: Polynomial coefficients for Python adc_to_temperature() function
%
% Constraint: max fitting error <= 1.5°C

clear; clc; close all;

%% 1. 读取数据
data = readtable('c:\Users\王应浩\OneDrive\桌面\ntcb.xlsx');
ADC = data.ADC;
TEMP = data.TEMP;
N = length(ADC);

fprintf('========== NTC ADC->温度 数据拟合 ==========\n');
fprintf('数据点: %d  |  ADC: %d~%d  |  温度: %d~%d °C\n', ...
    N, min(ADC), max(ADC), min(TEMP), max(TEMP));
fprintf('约束: 最大误差 ≤ 1.5°C\n\n');

%% 2. 扫描多项式阶数，找出满足要求的方案
fprintf('===== 多项式拟合扫描 =====\n');
fprintf('%-6s %-10s %-12s %-8s\n', '阶数', 'RMSE(°C)', 'MaxErr(°C)', '判定');
fprintf('%-6s %-10s %-12s %-8s\n', '----', '--------', '----------', '----');

max_order = 12;
results = [];
TOLERANCE = 1.5;

for n = 2:max_order
    [p, ~, mu] = polyfit(ADC, TEMP, n);  % mu用于中心化和缩放，提高数值稳定性
    TEMP_fit = polyval(p, ADC, mu);
    maxErr = max(abs(TEMP - TEMP_fit));
    rmse = sqrt(mean((TEMP - TEMP_fit).^2));
    pass = maxErr <= TOLERANCE;

    fprintf('%-6d %-10.4f %-12.4f %-8s\n', n, rmse, maxErr, ...
        char(9733) + " 合格" * pass + " 超标" * ~pass);

    results = [results; n, rmse, maxErr, pass];
end

%% 3. 选择最佳方案 (满足要求的最低阶数 + 推荐)
fprintf('\n===== 推荐方案 =====\n');

% 方案A: 满足要求的最低阶数
pass_rows = results(results(:,4) == 1, :);
if isempty(pass_rows)
    error('错误: 即使 %d阶也不能满足≤1.5°C要求，请考虑分段拟合', max_order);
end

% 最低阶数方案
min_order = pass_rows(1, 1);
[p_min, ~, mu_min] = polyfit(ADC, TEMP, min_order);
TEMP_fit_min = polyval(p_min, ADC, mu_min);
maxErr_min = max(abs(TEMP - TEMP_fit_min));

% 推荐方案: 选择maxError < 1.0 的最低阶数
recommend_rows = pass_rows(pass_rows(:,3) < 1.0, :);
if isempty(recommend_rows)
    rec_order = pass_rows(1, 1);
else
    rec_order = recommend_rows(1, 1);
end
[p_rec, ~, mu_rec] = polyfit(ADC, TEMP, rec_order);
TEMP_fit_rec = polyval(p_rec, ADC, mu_rec);
maxErr_rec = max(abs(TEMP - TEMP_fit_rec));
rmse_rec = sqrt(mean((TEMP - TEMP_fit_rec).^2));

fprintf('方案A (最低阶满足要求): %d阶, maxErr=%.4f°C\n', min_order, maxErr_min);
fprintf('方案B (推荐,余量充足): %d阶, maxErr=%.4f°C, RMSE=%.4f°C\n\n', ...
    rec_order, maxErr_rec, rmse_rec);

%% 4. 详细检查推荐方案每个点的误差
fprintf('===== 逐点误差检查 (%d阶多项式) =====\n', rec_order);
residuals = abs(TEMP - TEMP_fit_rec);
worst_idx = find(residuals == max(residuals), 1);
fprintf('最大误差点: ADC=%d, 真实温度=%d°C, 拟合温度=%.4f°C, 误差=%.4f°C\n', ...
    ADC(worst_idx), TEMP(worst_idx), TEMP_fit_rec(worst_idx), residuals(worst_idx));

% 统计误差分布
edges = [0, 0.1, 0.2, 0.5, 1.0, 1.5, inf];
fprintf('\n误差分布:\n');
for i = 1:length(edges)-1
    count = sum(residuals >= edges(i) & residuals < edges(i+1));
    pct = count / N * 100;
    if edges(i+1) == inf
        fprintf('  误差 >= %.1f°C: %d 点 (%.1f%%)\n', edges(i), count, pct);
    else
        fprintf('  误差 [%.1f, %.1f)°C: %d 点 (%.1f%%)\n', edges(i), edges(i+1), count, pct);
    end
end

%% 5. 输出多项式系数 (便于在单片机/C代码中使用)
fprintf('\n===== 多项式系数 (%d阶, 中心化+缩放) =====\n', rec_order);
fprintf('使用方式: temp = polyval(p, adc_value, mu)\n');
fprintf('或者手动计算:\n');
fprintf('  x_norm = (adc - %.10f) / %.10f\n', mu_min(1), mu_min(2));
fprintf('  temp = p(1)*x^%d + p(2)*x^%d + ... + p(%d)*x + p(%d)\n', ...
    rec_order, rec_order-1, rec_order, rec_order+1);
fprintf('\n系数 p = [\n');
fprintf('  %.12e\n', p_rec);
fprintf('];\n\n');

% 同时输出原始系数（不中心化），方便直接使用
[p_rec_raw, ~] = polyfit(ADC, TEMP, rec_order);
fprintf('原始系数 (直接使用, 无需mu):\n');
fprintf('p_raw = [\n');
fprintf('  %.12e\n', p_rec_raw);
fprintf('];\n');
fprintf('使用: temp = polyval(p_raw, adc_value);\n');

%% 6. 绘图
% 图1: 拟合曲线与误差
figure('Position', [50, 50, 1200, 500]);

subplot(1,2,1);
ADC_dense = linspace(min(ADC), max(ADC), 1000)';
TEMP_dense = polyval(p_rec, ADC_dense, mu_rec);
plot(ADC, TEMP, 'b.', 'MarkerSize', 5); hold on;
plot(ADC_dense, TEMP_dense, 'r-', 'LineWidth', 2);
xlabel('ADC 读数', 'FontSize', 11);
ylabel('温度 (°C)', 'FontSize', 11);
title(sprintf('NTC标定曲线 (%d阶, maxErr=%.4f°C)', rec_order, maxErr_rec), 'FontSize', 13);
legend('标定数据', sprintf('%d阶拟合', rec_order), 'Location', 'best');
grid on;

subplot(1,2,2);
plot(ADC, TEMP - TEMP_fit_rec, 'k.', 'MarkerSize', 5); hold on;
yline( TOLERANCE, 'r--', sprintf('+%.1f°C', TOLERANCE), 'LineWidth', 1.5);
yline(-TOLERANCE, 'r--', sprintf('-%.1f°C', TOLERANCE), 'LineWidth', 1.5);
yline(0, 'b-');
xlabel('ADC 读数', 'FontSize', 11);
ylabel('残差 (°C)', 'FontSize', 11);
title(sprintf('拟合残差 (max=%.4f°C, ≤%.1f°C ✓)', maxErr_rec, TOLERANCE), 'FontSize', 13);
grid on;
ylim([-2, 2]);

% 图2: 多阶对比
figure('Position', [50, 580, 1200, 450]);
plot_orders = [2, 3, 4, 5, rec_order];
for i = 1:length(plot_orders)
    n = plot_orders(i);
    [p_i, ~, mu_i] = polyfit(ADC, TEMP, n);
    err_i = abs(TEMP - polyval(p_i, ADC, mu_i));

    subplot(2, 3, i);
    plot(ADC, err_i, '.', 'MarkerSize', 4, 'Color', [0.2 0.4 0.7]); hold on;
    yline(TOLERANCE, 'r--', 'LineWidth', 1.2);
    ylabel('|误差| (°C)'); xlabel('ADC');
    ok_str = '✓ 合格'; if max(err_i) > TOLERANCE; ok_str = '✗ 超标'; end
    title(sprintf('%d阶 (max=%.3f°C) %s', n, max(err_i), ok_str));
    ylim([0, max(max(err_i)*1.2, TOLERANCE*1.5)]);
    grid on;
end
sgtitle('各阶多项式拟合误差对比', 'FontSize', 13);

fprintf('\n========== 完成 ==========\n');
