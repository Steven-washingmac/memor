%% TTAG 复测精度分析
% 读取 verify_xxx.xlsx，生成精度对比图
% 用法: 修改下方文件名，直接运行

clear; clc;

% ==================== 修改这里 ====================
xlsx_file = 'C:\Users\王应浩\OneDrive\桌面\TTAG_复测数据.xlsx';  % Excel 文件路径
device_id = '201154';                        % 设备号
% =================================================

% 读取 TTAG Verify 工作表
data = readtable(xlsx_file, 'Sheet', 'TTAG Verify', 'VariableNamingRule', 'preserve');
% 列: 1=序号, 2=目标(C), 3=水浴实际(C), 4=温度原始值, 5=原始值波动, 6=采样数, 7=计算温度(C), 8=误差(C), 9=通过, 10=测试时间

actual = data{:, 3};     % 水浴实际温度 (参考真值)
tag_temp = data{:, 7};   % 标签返回/计算温度
error = data{:, 8};      % 误差
target = data{:, 2};     % 目标温度

% 移除无效数据
valid = ~isnan(actual) & ~isnan(tag_temp) & ~isnan(error);
actual = actual(valid);
tag_temp = tag_temp(valid);
error = error(valid);
target = target(valid);

n = length(actual);
if n == 0
    error('没有有效数据，请检查 Excel 文件');
end

fprintf('数据点: %d\n', n);
fprintf('误差范围: %+.3f ~ %+.3f °C\n', min(error), max(error));
fprintf('平均误差: %+.4f °C\n', mean(error));
fprintf('标准差:   %.4f °C\n', std(error));
fprintf('|误差|<1°C: %d/%d (%.1f%%)\n', sum(abs(error)<1), n, sum(abs(error)<1)/n*100);

%% ========== 图1: 精度对比 ==========
figure('Position', [100, 100, 900, 400]);

subplot(1,2,1);
hold on;

% 理想线 y=x
t_min = min([actual; tag_temp]) - 2;
t_max = max([actual; tag_temp]) + 2;
plot([t_min, t_max], [t_min, t_max], 'k--', 'LineWidth', 1.5, 'DisplayName', '理想线 y=x');

% ±1°C 边界
fill([t_min, t_max, t_max, t_min], ...
     [t_min+1, t_max+1, t_max-1, t_min-1], ...
     [0.9 0.95 0.9], 'EdgeColor', 'none', 'FaceAlpha', 0.5, ...
     'DisplayName', '±1°C 区间');

% 数据点
scatter(actual, tag_temp, 40, abs(error), 'filled', 'MarkerEdgeColor', 'k', ...
        'LineWidth', 0.5, 'DisplayName', '复测点');
colormap(jet);
c = colorbar;
c.Label.String = '|误差| (°C)';
caxis([0, max(1.5, max(abs(error)))]);

xlabel('水浴实际温度 (°C)');
ylabel('标签温度 (°C)');
title(sprintf('TTAG %s 精度对比 (n=%d)', device_id, n));
legend('Location', 'best');
grid on;
axis equal;
xlim([t_min, t_max]);
ylim([t_min, t_max]);

%% 图1右: 误差分布
subplot(1,2,2);
hold on;

% ±1°C 线
yline(1, 'r--', 'LineWidth', 1.5);
yline(-1, 'r--', 'LineWidth', 1.5);
yline(0, 'k-', 'LineWidth', 1);

% 误差点
colors = zeros(n, 3);
colors(abs(error) <= 1, :) = repmat([0 0.6 0], sum(abs(error) <= 1), 1);    % 绿色: 通过
colors(abs(error) > 1, :) = repmat([0.9 0 0], sum(abs(error) > 1), 1);      % 红色: 超标

scatter(actual, error, 40, colors, 'filled', 'MarkerEdgeColor', 'k', 'LineWidth', 0.3);
xlabel('水浴实际温度 (°C)');
ylabel('误差 (°C)');
title(sprintf('误差分布 (max=%.3f°C, mean=%.4f°C, std=%.4f°C)', ...
      max(abs(error)), mean(error), std(error)));
grid on;

%% ========== 图2: 误差直方图 ==========
figure('Position', [150, 150, 500, 400]);
hold on;
histogram(error, 20, 'FaceColor', [0.2 0.4 0.7], 'EdgeColor', 'k', 'FaceAlpha', 0.8);
xline(-1, 'r--', 'LineWidth', 1.5);
xline(1, 'r--', 'LineWidth', 1.5);
xline(mean(error), 'g-', 'LineWidth', 2);
xlabel('误差 (°C)');
ylabel('频次');
title(sprintf('TTAG %s 误差直方图', device_id));
grid on;

% 保存图片
out_dir = 'C:\Users\王应浩\OneDrive\桌面';
saveas(1, fullfile(out_dir, 'TTAG_精度对比.png'));
saveas(2, fullfile(out_dir, 'TTAG_误差直方图.png'));
fprintf('\n图表已保存至桌面。\n');
