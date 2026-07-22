%% NTC ADC -> Temperature 6th Order Fit
% Constraint: max error <= 1.5C
clear; clc; close all;

%% Read CSV
data = readmatrix('ntcb.csv', 'NumHeaderLines', 1);
ADC = data(:, 1);
TEMP = data(:, 2);
N = length(ADC);

fprintf('===== NTC 6th-Order Polynomial Fit =====\n');
fprintf('Points: %d | ADC: %d~%d | Temp: %d~%d C\n\n', ...
    N, min(ADC), max(ADC), min(TEMP), max(TEMP));

%% Scale ADC to [-1, 1] for stability
ADC_min = min(ADC);
ADC_max = max(ADC);
ADC_norm = 2 * (ADC - ADC_min) / (ADC_max - ADC_min) - 1;

%% 6th-order fit
n = 6;
p_norm = polyfit(ADC_norm, TEMP, n);
TEMP_fit = polyval(p_norm, ADC_norm);

%% Error analysis
errors = abs(TEMP - TEMP_fit);
maxErr = max(errors);
rmse_val = sqrt(mean((TEMP - TEMP_fit).^2));
SSE = sum((TEMP - TEMP_fit).^2);
SST = sum((TEMP - mean(TEMP)).^2);
R2 = 1 - SSE/SST;

fprintf('Results:\n');
fprintf('  R2     = %.8f\n', R2);
fprintf('  RMSE   = %.6f C\n', rmse_val);
fprintf('  MaxErr = %.6f C\n\n', maxErr);

if maxErr <= 1.5
    fprintf('>>> PASS: maxErr=%.4f <= 1.5C <<<\n\n');
else
    fprintf('>>> FAIL: maxErr=%.4f > 1.5C <<<\n\n');
end

%% Error distribution
fprintf('Error distribution:\n');
for b = [0.1, 0.2, 0.5, 1.0, 1.5]
    cnt = sum(errors < b);
    fprintf('  < %.1fC: %d/%d (%.1f%%)\n', b, cnt, N, cnt/N*100);
end

%% Worst 3
[sorted_err, idx] = sort(errors, 'descend');
fprintf('\nWorst 3 points:\n');
for i = 1:3
    fprintf('  #%d: ADC=%d, True=%.2fC, Fit=%.4fC, Err=%.4fC\n', ...
        i, ADC(idx(i)), TEMP(idx(i)), TEMP_fit(idx(i)), sorted_err(i));
end

%% Coefficients
fprintf('\n========== Coefficients ==========\n');
fprintf('ADC range: [%.1f, %.1f]\n', ADC_min, ADC_max);
fprintf('Normalize: x = 2*(adc - %.1f)/(%.1f - %.1f) - 1\n', ADC_min, ADC_max, ADC_min);
fprintf('p = [\n');
for i = 1:length(p_norm)
    fprintf('    %+.12e\n', p_norm(i));
end
fprintf(']\n');
fprintf('Usage: temp = polyval(p, x);\n');

%% ===== SAVE FIGURES =====
fprintf('\nSaving figures...\n');

% Fig1: Fit curve + residuals
fig1 = figure('Position', [50, 50, 1200, 500], 'Visible', 'off');

subplot(1,2,1);
ADC_dense = (min(ADC):max(ADC))';
ADC_dense_norm = 2 * (ADC_dense - ADC_min) / (ADC_max - ADC_min) - 1;
TEMP_dense = polyval(p_norm, ADC_dense_norm);
plot(ADC, TEMP, 'b.', 'MarkerSize', 6); hold on;
plot(ADC_dense, TEMP_dense, 'r-', 'LineWidth', 2);
xlabel('ADC', 'FontSize', 12); ylabel('Temperature (C)', 'FontSize', 12);
title(sprintf('NTC Calibration - 6th Order  (R^2=%.6f, maxErr=%.4fC)', R2, maxErr), 'FontSize', 13);
legend('Calibration Data', '6th-Order Fit', 'Location', 'best');
grid on;

subplot(1,2,2);
plot(ADC, TEMP - TEMP_fit, 'k.', 'MarkerSize', 6); hold on;
yline(1.5, 'r--', '+1.5C', 'LineWidth', 1.5);
yline(-1.5, 'r--', '-1.5C', 'LineWidth', 1.5);
yline(0, 'b-');
xlabel('ADC', 'FontSize', 12); ylabel('Residual (C)', 'FontSize', 12);
title(sprintf('Residuals  (max=%.4fC <= 1.5C)', maxErr), 'FontSize', 13);
ylim([-2, 2]);
grid on;

saveas(fig1, 'ntc_fit_result.png');
fprintf('  Saved: ntc_fit_result.png\n');

% Fig2: Multi-order comparison
fig2 = figure('Position', [50, 580, 1200, 450], 'Visible', 'off');
plot_orders = [3, 4, 5, 6];
for i = 1:4
    on = plot_orders(i);
    [pi_fit, ~] = polyfit(ADC_norm, TEMP, on);
    err_i = abs(TEMP - polyval(pi_fit, ADC_norm));

    subplot(2, 2, i);
    plot(ADC, err_i, '.', 'MarkerSize', 4, 'Color', [0.2 0.4 0.7]); hold on;
    yline(1.5, 'r--', '1.5C', 'LineWidth', 1.2);
    ylabel('|Error| (C)'); xlabel('ADC');
    ok_str = 'PASS';
    if max(err_i) > 1.5; ok_str = 'FAIL'; end
    title(sprintf('Order %d (max=%.3fC) [%s]', on, max(err_i), ok_str));
    ylim([0, max(max(err_i)*1.2, 3)]);
    grid on;
end
sgtitle('Multi-Order Comparison (Error <= 1.5C)');

saveas(fig2, 'ntc_fit_compare.png');
fprintf('  Saved: ntc_fit_compare.png\n');

fprintf('\n===== Done =====\n');
