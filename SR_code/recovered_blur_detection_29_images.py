# -*- coding: utf-8 -*-
"""依据 29 张连续截图（633–661）整理的局部模糊检测完整代码。

忠于截图所示的原始流程和阈值。截图653到654之间有未展示的
卫星配置与路径解析，相关推断已用【缺图推断/待核对】标注。
未在真实卫星数据上进行运行验证。
"""

import glob
import os
import numpy as np
import cv2
import math
import multiprocessing
import sys
from skimage.morphology import square, opening, closing, dilation
from scipy import ndimage
from osgeo import gdal
# from lxml import etree as ET
import xml.etree.ElementTree as ET


def custom_filter(img):
    # 确保输入是二维数组
    if img.ndim != 2:
        raise ValueError("输入图像必须是二维数组")

    h, w = img.shape
    # 手动提取四个相邻像素的位置
    top_left = img[:-1, :-1]       # 左上角 [i,j]
    top_right = img[:-1, 1:]       # 右上角 [i,j+1]
    bottom_left = img[1:, :-1]     # 左下角 [i+1,j]
    bottom_right = img[1:, 1:]     # 右下角 [i+1,j+1]

    # 将四个位置堆叠成形状为(H-1, W-1, 4)的数组
    blocks = np.stack([
        top_left, top_right,
        bottom_left, bottom_right
    ], axis=-1)

    # 降序排序（沿最后一个轴）
    sorted_blocks = np.sort(blocks, axis=-1)[..., ::-1]

    # 取第二和第三大的元素（索引1和2）
    second = sorted_blocks[..., 1]
    third = sorted_blocks[..., 2]

    # 计算平均值并向上取整
    avg = (second + third) / 2
    result = np.ceil(avg).astype(img.dtype)
    return result


def sliding_window_max_numpy(A, window_size):
    pad_size = window_size // 2
    A_padded = np.pad(A, pad_size, mode='reflect')
    shape = (A.shape[0], A.shape[1], window_size, window_size)
    strides = A_padded.strides + A_padded.strides
    windows = np.lib.stride_tricks.as_strided(A_padded, shape=shape, strides=strides)
    return np.max(windows, axis=(2, 3))


def split_image_into_blocks(image_array, block_size):
    # 读取图像并转换为numpy数组
    height, width = image_array.shape
    block_height, block_width = block_size

    blocks = []
    indexs = []
    positions = []

    # check_id = open(os.path.join('/DiskArray/tmp/ytt/blurTest', 'check_code_INDEX.txt'), 'w', encoding='utf-8')
    for i in range(0, height, block_height):
        for j in range(0, width, block_width):
            end_i = min(i + block_height, height)
            end_j = min(j + block_width, width)
            if (end_i - i) != block_height:
                start_i = end_i - block_height
            else:
                start_i = i
            if (end_j - j) != block_width:
                start_j = end_j - block_width
            else:
                start_j = j
            block = image_array[start_i:end_i, start_j:end_j]
            blocks.append(block)

            index = (i / 1024) * math.ceil(width / block_width) + (j / 1024) + 1
            center_i = start_i + block_height // 2
            center_j = start_j + block_width // 2
            indexs.append(int(index))
            # str_result = f"i:{str(i)} j:{str(j)} index:{str(index)}"
            # if i == 0 and j == 0:
            #     check_id.write(str(width) + '\n')
            #     check_id.write(str(math.ceil(width / block_width)) + '\n')
            # check_id.write(str_result + '\n')
            positions.append((center_j, center_i))
            # print(f'块位置{i}, {j}')
    # check_id.close()
    return blocks, positions, indexs


def merge_blocks_to_image(datas_binary_matrix, img2_shape, block_size):
    """将分块的二值矩阵拼接回原始图像大小"""
    height, width = img2_shape
    block_height, block_width = block_size

    # 计算块的排列方式
    num_blocks_width = math.ceil(width / block_width)
    num_blocks_height = math.ceil(height / block_height)

    # 创建一个与img2相同大小的空矩阵
    full_binary_matrix = np.zeros((height, width), dtype=np.uint8)

    for i, binary_block in enumerate(datas_binary_matrix):
        # 计算当前块的行列索引
        row_idx = i // num_blocks_width
        col_idx = i % num_blocks_width

        # 计算块的真实起始坐标
        y_start = row_idx * block_height
        x_start = col_idx * block_width

        # 计算结束坐标，确保不超过图像边界
        y_end = min(y_start + block_height, height)
        x_end = min(x_start + block_width, width)

        # 计算实际的起始坐标（防止尺寸不一致）
        y_real_start = y_end - block_height
        x_real_start = x_end - block_width

        # 赋值回完整图像
        full_binary_matrix[y_real_start:y_end, x_real_start:x_end] = binary_block[
            :y_end - y_real_start, :x_end - x_real_start
        ]

    # full_binary_matrix = full_binary_matrix > 0.5
    # full_binary_matrix = dilation(full_binary_matrix, square(15))
    # full_binary_matrix = dilation(full_binary_matrix, square(5))
    # full_binary_matrix = full_binary_matrix * 255
    return full_binary_matrix


def compute_local_variance(img, window_size=15):
    img = img.astype(np.float64)
    kernel = np.ones((window_size, window_size), np.float64) / (window_size ** 2)
    # 计算均值和平方均值
    mean = cv2.filter2D(img, -1, kernel)
    mean_sq = cv2.filter2D(img ** 2, -1, kernel)
    localVal = (mean_sq - mean ** 2) * (window_size ** 2 / (window_size ** 2 - 1))
    windowSize_half = window_size // 2
    res = localVal[windowSize_half: -windowSize_half, windowSize_half: -windowSize_half]
    res = res.astype(np.float32)
    return np.clip(res, 0, None)


def process_image(var_map, mean_val):
    """
    图像处理流程：阈值处理 + 形态学开运算
    :param var_map: 局部方差图
    :param window_size: 形态学操作窗口大小（原始文档中的参数名）
    :return: 处理后的二值图像
    """
    # 动态计算阈值
    thresholded = np.where(var_map > mean_val, 0, var_map)
    window_size = 25
    # 形态学开运算
    selem = square(window_size)
    opened = opening(thresholded > 0, selem)
    return opened.astype(np.uint16)


def detect_stripe(matrix, threshold_ratio=0.8):
    rows, cols = matrix.shape
    total = np.sum(matrix)
    if total == 0:
        return None

    # 检测垂直条带
    col_sums = np.sum(matrix, axis=0)
    left, right = 0, cols - 1
    sum_left, sum_right = 0, 0
    left_window = right_window = None

    # 左侧窗口
    for c in range(cols):
        sum_left += col_sums[c]
        if sum_left >= threshold_ratio * total:
            left_window = (0, c)
            break
    # 右侧窗口
    for c in range(cols - 1, -1, -1):
        sum_right += col_sums[c]
        if sum_right >= threshold_ratio * total:
            right_window = (c, cols - 1)
            break

    # 选择更窄的窗口
    vertical = None
    if left_window and right_window:
        left_width = left_window[1] - left_window[0] + 1
        right_width = right_window[1] - right_window[0] + 1
        if left_width < right_width:
            vertical = left_window
        else:
            vertical = right_window
    elif left_window:
        vertical = left_window
    elif right_window:
        vertical = right_window

    # 验证垂直条带
    if vertical:
        start, end = vertical
        width = end - start + 1
        window = matrix[:, start:end + 1]
        rows_covered = np.sum(np.any(window, axis=1))  # 计算有多少行包含非零值
        if rows_covered >= rows * 0.8 and np.sum(window) > 0.7 * width * rows:
            return True
    return False


def binary_region_properties(binary_image):
    binary_image = binary_image > 0.5
    labeled, num_regions = ndimage.label(binary_image)
    region_sizes = ndimage.sum(binary_image, labeled, range(1, num_regions + 1))
    if not region_sizes.size:
        return 0, 0, 0

    max_region = np.max(region_sizes)
    len_region = len(region_sizes)
    sum_region = np.sum(region_sizes)
    return sum_region, max_region, len_region


def filter_connected_components(binary_matrix, threshold=3000):
    labeled_matrix, num_components = ndimage.label(binary_matrix)
    if num_components == 0:
        return binary_matrix.copy()
    areas = ndimage.sum(binary_matrix, labeled_matrix, range(num_components + 1))
    mask = areas >= threshold
    mask[0] = 0
    newMat = mask[labeled_matrix]
    return newMat.astype(binary_matrix.dtype)

    # for i in range(1, num_components + 1):
    #     component_pixels = (labeled_matrix == i).sum()
    #     if component_pixels < threshold:
    #         labeled_matrix[labeled_matrix == i] = 0
    # return labeled_matrix


def printstr(str):
    a = 0
    # print(str)


def blur_test_byvar(paras, pan_block, no_sr_block, block_water, block_cloud):
    """检测图像模糊区域并返回结果

    Args:
        pan_block: 全色图像块（MxN数组）
        no_sr_block: 未超分辨率图像块（MxN数组）

    Returns:
        tuple: （最大区域像素数，方差对比率，二值化区域矩阵）
    """
    # 保留原始尺寸用于后续对齐
    original_shape = no_sr_block.shape
    block_blur = np.zeros(original_shape, dtype=np.uint8)
    block_blur_mask = np.zeros(original_shape, dtype=np.uint8)
    block_blur_zeros = np.zeros(original_shape, dtype=np.uint8)
    # 边界填充处理
    pan_block = np.pad(pan_block, ((0, 1), (0, 1)), mode='constant')
    no_sr_block = np.pad(no_sr_block, ((0, 1), (0, 1)), mode='constant')

    # 自定义滤波处理
    pan_block = custom_filter(pan_block)
    no_sr_block = custom_filter(no_sr_block)
    rows, cols = pan_block.shape

    # 参数配置
    VAR_WIN_SIZE = 15
    MORPH_SIZE = 15
    VAR_THRESH = 0.025
    MIN_REGION_SIZE = paras["threshold_region"]

    # 计算局部方差
    local_var_no_sr = compute_local_variance(no_sr_block, VAR_WIN_SIZE)
    local_var_pan = compute_local_variance(pan_block, VAR_WIN_SIZE)
    original_var_no_sr = local_var_no_sr.copy()

    # 方差阈值处理
    local_var_pan = np.where(local_var_pan < VAR_THRESH, VAR_THRESH, local_var_pan)
    local_var_no_sr = np.where(local_var_no_sr < VAR_THRESH, VAR_THRESH, local_var_no_sr)

    def morphological_processing(ratio_map, mean_threshold):
        """形态学处理统一函数"""
        radius = (MORPH_SIZE - 1) // 2
        padded = np.pad(ratio_map, pad_width=radius, mode='constant', constant_values=0)
        opened = opening(padded > 0, square(MORPH_SIZE))
        closed = closing(opened, square(MORPH_SIZE))
        return closed[radius:-radius, radius:-radius]

    # 第一次阈值处理
    variance_ratio = local_var_no_sr / local_var_pan
    global_mean_ratio = 4 * np.nanmean(local_var_no_sr) / np.nanmean(local_var_pan)
    thresholded_ratio = np.where(variance_ratio < global_mean_ratio, 0, variance_ratio)
    closed_regions = morphological_processing(thresholded_ratio, global_mean_ratio)

    # 区域分析
    labeled, num_regions = ndimage.label(closed_regions)
    region_sizes = ndimage.sum(closed_regions, labeled, range(1, num_regions + 1))
    if not region_sizes.size:
        # print('第一次阈值处理区域不满足')
        return 0, 0, block_blur_zeros.tobytes()

    max_region = np.max(region_sizes)
    if max_region < MIN_REGION_SIZE:
        return 0, 0, block_blur_zeros.tobytes()

    # 二次阈值处理
    mean_ratio_2 = 0.5 * np.sum(variance_ratio * closed_regions) / np.sum(closed_regions)
    str_print = 'global_mean_ratio:' + str(global_mean_ratio) + ' mean_ratio_2:' + str(mean_ratio_2)
    # printstr(str_print)

    # 禁止1
    if global_mean_ratio < 1.1 and mean_ratio_2 < 1.1:
        print('阈值处理不满足条件')
        return 0, 0, block_blur_zeros.tobytes()

    adjusted_mean = 0.5 * mean_ratio_2 + 0.5 * global_mean_ratio

    if mean_ratio_2 > 10000:
        local_var_no_sr_max = np.max(local_var_no_sr)
        local_var_pan_max = np.max(local_var_pan)
        local_var_no_sr_log = 20 * np.log10(local_var_no_sr / local_var_no_sr_max)
        local_var_pan_log = 20 * np.log10(local_var_pan / local_var_pan_max)
        if np.nanmean(local_var_no_sr_log) > -30 and np.nanmean(local_var_pan_log) > -30:
            adjusted_mean = 30 * global_mean_ratio

    rethresholded = np.where(variance_ratio < adjusted_mean, 0, variance_ratio)
    closed_regions = morphological_processing(rethresholded, adjusted_mean)

    rethresholded1 = np.where(variance_ratio < 0.75 * adjusted_mean, 0, variance_ratio)
    closed_regions1 = morphological_processing(rethresholded1, adjusted_mean)

    # 最终区域分析
    labeled, num_regions = ndimage.label(closed_regions)
    region_sizes = ndimage.sum(closed_regions, labeled, range(1, num_regions + 1))
    if not region_sizes.size:
        print('第二次阈值处理区域不满足')
        return 0, 0, block_blur_zeros.tobytes()

    blur_region_max = np.max(region_sizes)

    # 给出模糊区域掩膜
    windowSize_half = (VAR_WIN_SIZE - 1) // 2
    block_blur_mask[
        windowSize_half:-windowSize_half, windowSize_half:-windowSize_half
    ] = closed_regions1
    block_blur_mask = filter_connected_components(block_blur_mask, threshold=2600)
    block_blur_mask = dilation(block_blur_mask, square(17))
    block_blur_mask[block_blur_mask > 0.5] = 255
    block_blur_mask[block_blur_mask <= 0.5] = 0

    # 第一步：根据不同卫星的情况，去除模糊面积较小的
    if "KF02" in paras["satellite"] and 2600 <= blur_region_max < MIN_REGION_SIZE:
        print('第二次阈值处理区域面积较小!')
        return 0, 0, block_blur_mask.tobytes()

    # 第一步：根据不同卫星的情况，去除模糊面积较小的
    if blur_region_max < MIN_REGION_SIZE:
        return 0, 0, block_blur_zeros.tobytes()

    '''
        计算模糊区域的参数 calculate_paras
    '''
    calculate_paras = dict(
        param1=-1.0, param2=-1.0, param3=-1.0, param4=-1.0,
        param5=-1.0, param6=-1.0, param7=-1.0, param8=-1.0,
        param9=-1.0, param10=-1.0, param11=-1.0, param12=-1.0,
        param13=-1.0, param14=-1.0, param15=-1.0, param16=-1.0,
        param17=-1.0, param18=-1.0, param19=-1.0, param20=-1.0
    )
    # param1: 第一次阈值处理：全局
    # param2: 第二次阈值处理：模糊区域
    # param3: 模糊区域面积
    # param4: 模糊区域最大块面积
    # param5: 模糊区域块数
    # param6: 模糊区域占整张图面积比值
    # param7: NOSR中非模糊区域/模糊区域
    # param8: NOSR整张图的log值
    # param9: PAN整张图的log值
    # param10: 模糊区域（过滤后）占模糊区域比值
    # param11: 模糊区域（过滤后）占NOSR区域比值
    # param12: NOSR模糊区域（过滤后）log值
    # param13: PAN模糊区域（过滤后）log值
    # param14: NOSR模糊区域（过滤后）- PAN模糊区域（过滤后）
    # param15: NOSR整图分布占比
    # param16: NOSR_area面积
    # param17: NOSR膨胀后面积
    # param18: NOSR膨胀后面积增加比
    calculate_paras['param1'] = global_mean_ratio
    calculate_paras['param2'] = mean_ratio_2

    # 方差对比计算
    region_var_mean = np.sum(closed_regions * original_var_no_sr) / np.sum(closed_regions)
    variance_ratio = np.nanmean(original_var_no_sr) / region_var_mean

    str_print = 'variance_ratio:' + str(variance_ratio)
    printstr(str_print)
    calculate_paras['param7'] = variance_ratio

    if variance_ratio > 20:
        # print('模糊区域值是原图中较小方差区域')
        if "JL1KF02" in paras["satellite"]:
            return 0, 0, block_blur_mask.tobytes()
        else:
            return 0, 0, block_blur_zeros.tobytes()

    def countCoverageBlocks(matrix1, k):
        rows, cols = matrix1.shape
        kkk = rows % k
        matrix = matrix1[kkk:rows, kkk:cols]
        rows, cols = matrix.shape
        m = rows // k
        n = cols // k
        matrix_reshaped = matrix.reshape(m, k, n, k)
        matrix_blocks = matrix_reshaped.transpose(1, 3, 0, 2)
        matrix_blocks = matrix_blocks.reshape(k * k, m * n)
        block_has_one = np.any(matrix_blocks, axis=0)
        block_num = np.sum(block_has_one)
        total_count = m * n
        block_coverage = block_num / total_count
        return block_coverage

    blur_region_dist = countCoverageBlocks(closed_regions, 100)
    blur_region_sum = np.sum(closed_regions)
    blur_region_size = len(region_sizes)
    str_print = ('blur_region_sum:' + str(blur_region_sum)
                 + ' blur_region_max:' + str(blur_region_max)
                 + ' blur_region_size:' + str(blur_region_size)
                 + ' blur_region_dist:' + str(blur_region_dist))
    printstr(str_print)
    calculate_paras['param3'] = blur_region_sum
    calculate_paras['param4'] = blur_region_max
    calculate_paras['param5'] = blur_region_size
    calculate_paras['param6'] = blur_region_dist

    if blur_region_max > 750000 and blur_region_dist > 0.75:
        print('模糊区域过大1')
        if "JL1KF02" in paras["satellite"]:
            return 0, 0, block_blur_mask.tobytes()
        else:
            return 0, 0, block_blur_zeros.tobytes()

    if blur_region_dist > 0.82:
        print('模糊区域过大2')
        if "JL1KF02" in paras["satellite"]:
            return 0, 0, block_blur_mask.tobytes()
        else:
            return 0, 0, block_blur_zeros.tobytes()

    # 禁止 closed_regions 避免条纹
    closed_dilation = dilation(closed_regions, square(21))
    closed_dilation = np.array(closed_dilation, dtype=int)
    rows, cols = closed_dilation.shape
    top_sum = np.sum(closed_dilation[0:15, 0:cols])
    behind_sum = np.sum(closed_dilation[rows - 16:rows - 1, 0:cols])
    left_sum = np.sum(closed_dilation[0:rows, 0:15])
    right_sum = np.sum(closed_dilation[0:rows, cols - 16:cols - 1])
    condition_1 = top_sum != 0 and behind_sum != 0
    condition_2 = left_sum != 0 or right_sum != 0
    if condition_1 and condition_2:
        if detect_stripe(closed_dilation, threshold_ratio=0.8):
            print('由于竖直条带被去除')
            if "JL1KF02" in paras["satellite"]:
                return 0, 0, block_blur_mask.tobytes()
            else:
                return 0, 0, block_blur_zeros.tobytes()

    local_var_no_sr_max = np.max(local_var_no_sr)
    local_var_pan_max = np.max(local_var_pan)
    local_var_no_sr_log = 20 * np.log10(local_var_no_sr / local_var_no_sr_max)
    local_var_pan_log = 20 * np.log10(local_var_pan / local_var_pan_max)

    # 后续操作
    local_var_no_sr_pass = local_var_no_sr_log.copy()
    local_var_no_sr_pass = np.where(local_var_no_sr_pass < -100, -100, local_var_no_sr_pass)
    mean_pass = 0.9 * np.sum(np.sum(local_var_no_sr_pass * closed_regions)) / np.sum(closed_regions)
    local_var_no_sr_pass = np.where(local_var_no_sr_pass >= mean_pass, 0, local_var_no_sr_pass)
    local_var_no_sr_pass = np.where(local_var_no_sr_pass < mean_pass, 1, local_var_no_sr_pass)
    radius_pass = (5 - 1) // 2
    local_var_no_sr_pass = np.pad(
        local_var_no_sr_pass, pad_width=radius_pass,
        mode='constant', constant_values=0
    )
    local_var_no_sr_pass = opening(local_var_no_sr_pass > 0, square(5))
    local_var_no_sr_pass = local_var_no_sr_pass[
        radius_pass:-radius_pass, radius_pass:-radius_pass
    ]
    closed_and_local_var_no_sr_pass = local_var_no_sr_pass * closed_regions
    closed_and_local_var_no_sr_pass_sum = np.sum(closed_and_local_var_no_sr_pass)
    local_var_no_sr_pass_sum = np.sum(local_var_no_sr_pass)
    closed_sum = np.sum(closed_regions)
    nosr_log_ratio = closed_and_local_var_no_sr_pass_sum / local_var_no_sr_pass_sum
    blur_log_ratio = closed_and_local_var_no_sr_pass_sum / closed_sum
    str_print = ('blur_log_ratio:' + str(blur_log_ratio)
                 + ' nosr_log_ratio:' + str(nosr_log_ratio))
    printstr(str_print)

    closed_and_local_var_no_sr_flag = 0
    if blur_log_ratio > 0.9 and nosr_log_ratio > 0.1:
        closed_and_local_var_no_sr_flag = 1

    pan_blur_filter_log_mean = np.sum(
        closed_and_local_var_no_sr_pass * local_var_pan_log
    ) / np.sum(closed_and_local_var_no_sr_pass)
    no_sr_blur_filter_log_mean = np.sum(
        closed_and_local_var_no_sr_pass * local_var_no_sr_log
    ) / np.sum(closed_and_local_var_no_sr_pass)
    no_sr_log_mean = np.mean(local_var_no_sr_log)
    pan_log_mean = np.mean(local_var_pan_log)
    no_sr_pan_log_mean = no_sr_blur_filter_log_mean - pan_blur_filter_log_mean

    str_print1 = ('no_sr_log_mean:' + str(no_sr_log_mean)
                  + ' pan_log_mean:' + str(pan_log_mean))
    str_print2 = (' no_sr_blur_filter_log_mean:' + str(no_sr_blur_filter_log_mean)
                  + ' pan_blur_filter_log_mean:' + str(pan_blur_filter_log_mean)
                  + ' no_sr_pan_log_mean:' + str(no_sr_pan_log_mean))
    printstr(str_print1 + str_print2)

    calculate_paras['param8'] = no_sr_log_mean
    calculate_paras['param9'] = pan_log_mean
    calculate_paras['param10'] = blur_log_ratio
    calculate_paras['param11'] = nosr_log_ratio
    calculate_paras['param12'] = no_sr_blur_filter_log_mean
    calculate_paras['param13'] = pan_blur_filter_log_mean
    calculate_paras['param14'] = no_sr_pan_log_mean

    local_var_no_sr_pass_dilation = dilation(local_var_no_sr_pass, square(15))
    # 指标1
    local_var_no_sr_pass_para1 = 0
    if np.sum(local_var_no_sr_pass) >= 500000:
        local_var_no_sr_pass_para1 = 1
    local_var_no_sr_pass_para1_t = (
        np.sum(local_var_no_sr_pass_dilation) - np.sum(local_var_no_sr_pass)
    ) / np.sum(local_var_no_sr_pass)
    if local_var_no_sr_pass_para1_t > 0.75:
        local_var_no_sr_pass_para1 = 1

    # 指标2
    local_var_no_sr_pass_para2 = 0
    local_var_no_sr_pass_para2_t = countCoverageBlocks(local_var_no_sr_pass, 100)
    str1 = 'local_var_no_sr_pass_para2_t:' + str(local_var_no_sr_pass_para2_t)
    printstr(str1)
    calculate_paras['param15'] = local_var_no_sr_pass_para2_t
    # printstr(calculate_paras)

    # 第二步：根据不同卫星的情况，去掉模糊程度较轻的
    if "JL1GF04" in paras["satellite"]:
        if no_sr_pan_log_mean < paras["threshold_var"]:
            return 0, 0, block_blur_zeros.tobytes()
        if no_sr_pan_log_mean < 27 and local_var_no_sr_pass_para2_t < 0.95:
            return 0, 0, block_blur_zeros.tobytes()

    condition_0325_0 = 0
    if (no_sr_log_mean - pan_log_mean < -0.9
            and no_sr_pan_log_mean > 12
            and no_sr_log_mean > -47.1
            and blur_region_size < 7 and blur_region_dist < 0.15):
        condition_0325_0 = 1
    if no_sr_pan_log_mean < 13.85:
        # 0325日数据
        if (condition_0325_0 == 1 and no_sr_pan_log_mean > 12.00
                and no_sr_log_mean - no_sr_blur_filter_log_mean > 1.5
                and local_var_no_sr_pass_para2_t > 0.98
                and no_sr_blur_filter_log_mean > -50.2):
            print('3月25日模糊数据')
        elif (condition_0325_0 == 1 and no_sr_pan_log_mean > 12.00
                and no_sr_log_mean - no_sr_blur_filter_log_mean > 1.5
                and local_var_no_sr_pass_para2_t > 0.82
                and no_sr_blur_filter_log_mean > -50.2
                and "JL1KF01" in paras["satellite"]):
            print('KF01轻微模糊数据')
        else:
            print('模糊程度较低')
            return 0, 0, block_blur_zeros.tobytes()

    condition_0325 = 0
    if (no_sr_blur_filter_log_mean > -71.5 and no_sr_log_mean > -70
            and no_sr_pan_log_mean > 19
            and local_var_no_sr_pass_para2_t > 0.98
            and blur_region_size < 7):
        condition_0325 = 1

    if (no_sr_blur_filter_log_mean < -60 and no_sr_log_mean < -60
            and "JL1KF02" in paras["satellite"]):
        if (no_sr_blur_filter_log_mean > -67 and no_sr_log_mean > -70
                and no_sr_log_mean - no_sr_blur_filter_log_mean > 0):
            print('3月23日模糊数据')
            # return 1, 1, block_blur_mask.tobytes()
        elif (condition_0325 == 1 and blur_region_dist < 0.26
                and no_sr_log_mean - pan_log_mean < 2.7):
            print('3月25日模糊数据')
            # return 1, 1, block_blur_mask.tobytes()
        else:
            print('模糊区域是水域/雪山/云/云影')
            return 0, 0, block_blur_mask.tobytes()

    if no_sr_pan_log_mean < 20 and blur_region_sum > 600000:
        print('模糊区域不明显且面积较大')
        if "JL1KF02" in paras["satellite"]:
            return 0, 0, block_blur_mask.tobytes()
        else:
            return 0, 0, block_blur_zeros.tobytes()

    if local_var_no_sr_pass_para2_t < 0.5 and variance_ratio > 12:
        print('图像中存在阴影区域')
        if "JL1KF02" in paras["satellite"]:
            return 0, 0, block_blur_mask.tobytes()
        else:
            return 0, 0, block_blur_zeros.tobytes()

    '''
        再添加水、云掩膜之前，找出包括水、云区域中的典型模糊
    '''
    # 典型模糊
    if (no_sr_log_mean > -45 and no_sr_blur_filter_log_mean > -45
            and local_var_no_sr_pass_para2_t > 0.7):
        print('典型模糊情况1')
        return blur_region_max, blur_region_max, block_blur_mask.tobytes()

    # 七室模糊（保留原截图注释）
    if (no_sr_log_mean > -45 and no_sr_blur_filter_log_mean > -60
            and local_var_no_sr_pass_para2_t >= 0.81
            and no_sr_pan_log_mean > 20):
        print('典型模糊情况2')
        return blur_region_max, blur_region_max, block_blur_mask.tobytes()

    windowSize_half = (VAR_WIN_SIZE - 1) // 2
    block_blur[
        windowSize_half:-windowSize_half, windowSize_half:-windowSize_half
    ] = closed_regions
    block_blur = filter_connected_components(block_blur, threshold=3000)
    block_blur = dilation(block_blur, square(17))
    block_blur[block_blur > 0.5] = 255
    block_blur[block_blur <= 0.5] = 0

    '''
        下面是添加云掩膜
    '''
    block_cloud = dilation(block_cloud, square(15))
    condition_cloud = (block_blur == 255) & (block_cloud == 255)
    block_blur_filter_cloud = block_blur.copy()
    block_blur_filter_cloud[condition_cloud] = 0
    sum_region_filter_cloud, max_region_filter_cloud, len_region_filter_cloud = binary_region_properties(
        block_blur_filter_cloud
    )
    if max_region_filter_cloud < MIN_REGION_SIZE:
        print('由于云掩膜去除')
        return 0, 0, block_blur_zeros.tobytes()

    '''
        KF02水上模糊较多，水掩膜失效
    '''
    # KF02
    if "JL1KF02" in paras["satellite"]:
        block_water = dilation(block_water, square(15))
        block_blur_filter_cloud_water = block_blur_filter_cloud.copy()
        condition_water = (block_blur_filter_cloud == 255) & (block_water == 255)
        block_blur_filter_cloud_water[condition_water] = 0
        sum_region_filter_cloud_water, max_region_filter_cloud_water, len_region_filter_cloud_water = binary_region_properties(
            block_blur_filter_cloud_water
        )
        if max_region_filter_cloud_water < MIN_REGION_SIZE:
            print('由于水掩膜去除--不去除')
            # return 0, 0, block_blur_zeros.tobytes()

        if (no_sr_log_mean > -68 and no_sr_blur_filter_log_mean > -77
                and local_var_no_sr_pass_para2_t >= 0.81
                and no_sr_pan_log_mean > 20):
            if pan_log_mean > -68 and blur_region_size < 5:
                print('KF02系列模糊3')
                return blur_region_max, blur_region_max, block_blur_mask.tobytes()

        if (no_sr_log_mean > -40 and no_sr_blur_filter_log_mean > -52
                and no_sr_pan_log_mean > 17):
            if pan_log_mean > -53 and blur_region_size < 5:
                print('KF02系列模糊4')
                return blur_region_max, blur_region_max, block_blur_mask.tobytes()

    '''
        下面是添加水掩膜
    '''
    if "JL1KF01" in paras["satellite"]:
        block_water = dilation(block_water, square(15))
        block_blur_filter_cloud_water = block_blur_filter_cloud.copy()
        condition_water = (block_blur_filter_cloud == 255) & (block_water == 255)
        block_blur_filter_cloud_water[condition_water] = 0
        sum_region_filter_cloud_water, max_region_filter_cloud_water, len_region_filter_cloud_water = binary_region_properties(
            block_blur_filter_cloud_water
        )
        if max_region_filter_cloud_water < MIN_REGION_SIZE:
            print('由于水掩膜去除')
            return 0, 0, block_blur_zeros.tobytes()

        if (no_sr_log_mean > -68 and no_sr_blur_filter_log_mean > -77
                and local_var_no_sr_pass_para2_t >= 0.81
                and no_sr_pan_log_mean > 17):
            if pan_log_mean > -68 and blur_region_size < 5:
                print('KF01系列模糊3')
                return blur_region_max, blur_region_max, block_blur_mask.tobytes()

        if (no_sr_log_mean > -40 and no_sr_blur_filter_log_mean > -52
                and no_sr_pan_log_mean > 12.65):
            if pan_log_mean > -53 and blur_region_size < 5:
                print('KF01系列模糊4')
                return blur_region_max, blur_region_max, block_blur_mask.tobytes()

    # if calculate_paras['param14'] < paras["threshold_var"]:
    #     print('模糊程度较低')
    #     if calculate_paras['param14'] > 10 and "JL1KF" in paras["satellite"]:
    #         return 0, 0, block_blur_mask.tobytes()
    #     else:
    #         return 0, 0, block_blur_zeros.tobytes()
    if calculate_paras['param14'] < 11.90:
        print('模糊程度较低')
        if calculate_paras['param14'] > 10 and "JL1KF" in paras["satellite"]:
            return 0, 0, block_blur_mask.tobytes()
        else:
            return 0, 0, block_blur_zeros.tobytes()
    print('进入第二次过滤')
    return 1, 1, block_blur_mask.tobytes()


def blur_test_byfre(block1PAN, block2NoSR):
    # Image dimensions (assuming both images are the same size)

    # 参数设置
    var_win_size = 15
    rows, cols = block1PAN.shape
    # 计算局部方差
    local_var_no_sr = compute_local_variance(block2NoSR, var_win_size)
    local_var_pan = compute_local_variance(block1PAN, var_win_size)

    # 处理图像
    mean_val1 = np.mean(local_var_no_sr)
    local_var_pankuo = local_var_pan
    mean_val2 = np.mean(local_var_pankuo)
    mean_val = min(mean_val1, mean_val2)
    if mean_val > 1000:
        mean_val = mean_val * 3 / 20

    processed_pan = process_image(local_var_pankuo, mean_val)
    if np.sum(processed_pan) > 400000:
        return 0, 0
    local_var_no_sr_max = np.max(local_var_no_sr)
    local_var_pan_max = np.max(local_var_pan)
    if local_var_no_sr_max == 0:
        return 0, 0
    if local_var_pan_max == 0:
        return 0, 0
    local_var_no_sr = np.where(local_var_no_sr == 0, 0.0000001, local_var_no_sr)
    local_var_pan = np.where(local_var_pan == 0, 0.0000001, local_var_pan)
    local_var_no_sr = 20 * np.log10(local_var_no_sr / local_var_no_sr_max)
    local_var_pan = 20 * np.log10(local_var_pan / local_var_pan_max)
    rows1, cols1 = local_var_pan.shape
    # Image dimensions (assuming both images are the same size)
    N = rows1
    M = cols1

    # Create meshgrid
    X, Y = np.meshgrid(np.arange(1, N + 1), np.arange(1, M + 1))
    D0_low = 10
    D0_high = 110  # High-frequency cutoff
    sigma_low = D0_low / (2 * math.sqrt(2 * math.log(2)))
    sigma_high = D0_high / (2 * math.sqrt(2 * math.log(2)))

    # Distance matrix
    D = np.sqrt((X - N / 2) ** 2 + (Y - M / 2) ** 2)

    # Gaussian low pass filter
    H_low = np.exp(-(D ** 2) / (2 * sigma_low ** 2))

    # Gaussian high pass filter
    H_high = 1 - np.exp(-(D ** 2) / (2 * sigma_high ** 2))

    # Band-pass filter
    H_bandpass = H_low * H_high * 20

    # Perform FFT and shift
    img1_fft = np.fft.fftshift(np.fft.fft2(local_var_pan))
    img2_fft = np.fft.fftshift(np.fft.fft2(local_var_no_sr))
    # Apply band-pass filter in frequency domain
    img1_fft_2 = img1_fft * H_bandpass
    img2_fft_2 = img2_fft * H_bandpass
    # Inverse FFT to get the spatial domain result
    img1_out = np.abs(np.fft.ifft2(np.fft.ifftshift(img1_fft_2)))
    img2_out = np.abs(np.fft.ifft2(np.fft.ifftshift(img2_fft_2)))
    # Convert to round for saving
    img1_out1 = np.round(img1_out)
    img2_out1 = np.round(img2_out)
    if (np.max(img1_out1) > 65535 or np.min(img1_out1) < 0
            or np.max(img2_out1) > 65535 or np.min(img2_out1) < 0):
        print('error')

    n_len = rows1
    k_len = 29
    k_bianjie = (k_len - 1) // 2
    # windows = np.lib.stride_tricks.sliding_window_view(img2_out1, (k_len, k_len))
    max_img2_out1 = sliding_window_max_numpy(img2_out1, k_len)

    img1_out1[img1_out1 > 0] = 1
    max_img2_out1[max_img2_out1 > 0] = 1
    img3_tmp = (
        img1_out1[k_bianjie:n_len - k_bianjie, k_bianjie:n_len - k_bianjie]
        - max_img2_out1[k_bianjie:n_len - k_bianjie, k_bianjie:n_len - k_bianjie]
    )

    img3_tmp[img3_tmp <= 0] = 0
    if np.sum(img3_tmp) == 0:
        return 0, 0

    labeled_mat, num_features = ndimage.label(img3_tmp)
    # 标记连通域
    sizes = ndimage.sum(img3_tmp, labeled_mat, range(1, num_features + 1))
    res = max(sizes)
    if len(sizes) > 15:
        return 0, 0
    if (np.sum(img1_out1) > 500000 or np.sum(max_img2_out1) > 500000
            or res / np.sum(img3_tmp) < 0.3):
        return 0, 0

    return res, res


def blur_test_wrapper_byfre(i, index, block1, block2):
    data_sum_value, data_var_value = blur_test_byfre(block1, block2)
    return i, index, data_sum_value, data_var_value


def parallel_process_byfre(indexs, block1s, block2s, data_sum, data_var, data_index):
    # 将所有任务打包成元组
    tasks = [(i, index, block1s[i], block2s[i]) for i, index in enumerate(indexs)]

    with multiprocessing.Pool(processes=24) as pool:
        results = pool.starmap(blur_test_wrapper_byfre, tasks)
    for i, index, data_sum_i, data_var_i in results:
        data_sum[i] = data_sum_i
        data_var[i] = data_var_i
        data_index[i] = index


def blur_test_wrapper_byvar(i, index, paras, block1, block2, block_water, block_cloud):
    data_sum_value, data_var_value, data_blur_value = blur_test_byvar(
        paras, block1, block2, block_water, block_cloud
    )
    return i, index, data_sum_value, data_var_value, data_blur_value


def parallel_process_byvar(indexs, paras, block1s, block2s, blocks_water, blocks_cloud,
                           data_sum, data_var, data_blur, data_index):
    # 将所有任务打包成元组
    tasks = [
        (i, index, paras, block1s[i], block2s[i], blocks_water[i], blocks_cloud[i])
        for i, index in enumerate(indexs)
    ]

    with multiprocessing.Pool(processes=24) as pool:
        results = pool.starmap(blur_test_wrapper_byvar, tasks)

    for i, index, data_sum_i, data_var_i, data_blur_i in results:
        data_sum[i] = data_sum_i
        data_var[i] = data_var_i
        data_index[i] = index
        data_blur[i] = np.frombuffer(data_blur_i, dtype=np.uint8).reshape((1024, 1024))


def read_by_gdal(img_path):
    dataset = gdal.Open(img_path)
    w = dataset.RasterXSize
    h = dataset.RasterYSize
    image = dataset.ReadAsArray(0, 0, w, h)
    dataset = None
    return image


def read_mss_meta_xml(MSS_meta_path):
    if not os.path.isfile(MSS_meta_path):
        print('请输出正确的xml路径')
        # sys.exit(1)
    # 将其写入req.xml
    # tree = etree.parse(MSS_meta_path)
    tree = ET.parse(MSS_meta_path)

    root = tree.getroot()
    ProcessInfo_root = root.find("ProcessInfo")
    AbsCalibrationGain_root = ProcessInfo_root.find("AbsCalibrationGain")
    AbsCalibrationGain_text = AbsCalibrationGain_root.text
    AbsCalibrationBias_root = ProcessInfo_root.find("AbsCalibrationBias")
    AbsCalibrationBias_text = AbsCalibrationBias_root.text
    return AbsCalibrationGain_text, AbsCalibrationBias_text


def get_cloud_mask(input_path):
    cloud_tif_path = glob.glob(os.path.join(input_path, '*_L1_PAN_cloud.TIF'))
    if len(cloud_tif_path) != 1:
        print(f'寻找ID:{os.path.basename(input_path)}的_L1_PAN_cloud.TIF错误')
    cloud_tif_path = cloud_tif_path[0]
    cloud_mask = read_by_gdal(cloud_tif_path)
    return cloud_mask


def get_shadow_mask(input_path):
    debug_path = os.path.join(input_path, 'Debug')
    shadow_tif_path = glob.glob(os.path.join(debug_path, 'finalShadow.tif'))
    if len(shadow_tif_path) != 1:
        print(f'寻找ID:{os.path.basename(input_path)}的_L1_PAN_cloud.TIF错误')
    shadow_tif_path = shadow_tif_path[0]
    shadow_mask = read_by_gdal(shadow_tif_path)
    shadow_mask[shadow_mask < 1] = 255
    shadow_mask[shadow_mask < 255] = 0
    return shadow_mask


def get_water_mask(MSS_input_path):
    MSS_tif_path = glob.glob(os.path.join(MSS_input_path, '*_L1_MSS.tif'))
    MSS_meta_path = glob.glob(os.path.join(MSS_input_path, '*_L1_MSS_meta.xml'))
    # print(MSS_input_path)
    if len(MSS_tif_path) != 1 or len(MSS_meta_path) != 1:
        print(f'寻找ID:{os.path.basename(MSS_input_path)}的MSS.tif和meta.xml错误')
    MSS_tif_path = MSS_tif_path[0]
    MSS_meta_path = MSS_meta_path[0]
    AbsCalibrationGain_text, AbsCalibrationBias_text = read_mss_meta_xml(MSS_meta_path)
    gain_list = AbsCalibrationGain_text.split(',')
    AbsCalibrationGain = np.array([float(x) for x in gain_list], dtype=np.float32)
    gain_list = AbsCalibrationBias_text.split(',')
    AbsCalibrationBias = np.array([float(x) for x in gain_list], dtype=np.float32)
    dataset = gdal.Open(MSS_tif_path)
    # 获取第二个和第四个波段
    band2 = dataset.GetRasterBand(2)
    band4 = dataset.GetRasterBand(4)
    data2 = band2.ReadAsArray().astype(np.float32)
    data4 = band4.ReadAsArray().astype(np.float32)
    dataset = None
    Im2 = data2 * AbsCalibrationGain[1] + AbsCalibrationBias[1]  # 注意Python的索引从0开始
    Im4 = data4 * AbsCalibrationGain[3] + AbsCalibrationBias[3]
    Pndwi = (Im2 - Im4) / (Im2 + Im4)
    Imask1 = Pndwi
    # print(np.max(Imask1))
    threshold = 0.3
    Imask1[Imask1 < threshold] = 0
    Imask1[Imask1 >= threshold] = 255
    # # Imask1[Imask1 < threshold] = Imask1[Imask1 < threshold] / threshold * 255
    # cv2.imwrite('1.tif', Imask1.astype(np.uint8))
    return Imask1.astype(np.uint8)
    # Imask1[Pndwi >= 0.3] = 1
    # Imask1[Pndwi < 0.1] = 0


def resize_bool_matrix(src, dsize):
    """
    无损调整布尔矩阵尺寸

    参数:
    src : 原始布尔矩阵（np.ndarray, dtype=bool）
    dsize : 目标尺寸（width, height）元组

    返回:
    dst : 目标布尔矩阵（np.ndarray, dtype=bool）
    """
    # 转换为uint8图像（True→255，False→0）
    uint8_img = src.astype(np.uint8)

    # 执行最近邻插值缩放
    resized = cv2.resize(
        uint8_img,
        dsize=dsize,  # (width, height)
        interpolation=cv2.INTER_NEAREST
    )

    # 恢复布尔矩阵
    resized[resized >= 128] = 255
    resized[resized < 128] = 0
    return resized


def read_meta_xml(xml_path):
    if not os.path.isfile(xml_path):
        print("请输出正确的xml路径")
        # sys.exit(1)
    # 将其写入req.xml
    tree = ET.parse(xml_path)
    root = tree.getroot()
    ProductInfo_root = root.find("ProductInfo")
    ProductQuality_root = ProductInfo_root.find("ProductQuality")
    ProductQuality_text = ProductQuality_root.text
    CloudPercent_root = ProductInfo_root.find("CloudPercent")
    CloudPercent_text = CloudPercent_root.text
    return ProductQuality_text, CloudPercent_text


def process_folder_KF(folder_path, outputPath):
    # 建立处理参数字典
    paras = {"satellite": "", "threshold_var": 0, "threshold_region": 0}

    # 读取图像并预处理
    if "JL1GF04A" in os.path.basename(folder_path):
        img1_path = glob.glob(os.path.join(folder_path, 'PAN.tif'))
        img2_path = glob.glob(os.path.join(folder_path, 'PAN_ori.tif'))
        paras["satellite"] = "JL1GF04A"
        paras["threshold_var"] = 22
        paras["threshold_region"] = 4000
        # if len(img2_path) != 1
    elif "JL1KF01" in os.path.basename(folder_path):
        img1_path = glob.glob(os.path.join(folder_path, '*_L1_PAN.tif'))
        # [缺图推断：653与654之间未展示以下路径和参数配置]
        img2_path = glob.glob(os.path.join(folder_path, '*_NOSR.tif'))
        paras["satellite"] = "JL1KF01"
        paras["threshold_var"] = 22  # [待核对：此处阈值不可由截图确认]
        paras["threshold_region"] = 4000  # [待核对]
    elif "JL1KF02" in os.path.basename(folder_path):
        # [缺图推断：该卫星分支配置在截屏缺口中，匹配方式与阈值待核对]
        img1_path = glob.glob(os.path.join(folder_path, '*_L1_PAN.tif'))
        img2_path = glob.glob(os.path.join(folder_path, '*_NOSR.tif'))
        paras["satellite"] = "JL1KF02"
        paras["threshold_var"] = 22  # [待核对]
        paras["threshold_region"] = 4000  # [待核对]
    else:
        raise ValueError(f"未识别的卫星目录: {folder_path}")

    # [缺图推断：截图653用glob返回列表，654直接传给read_by_gdal；
    #  因此中间缺失处应包含取出唯一文件路径的逻辑]
    if len(img1_path) != 1 or len(img2_path) != 1:
        raise FileNotFoundError(
            f"PAN/对照影像数量不正确: {folder_path}; "
            f"img1={img1_path}, img2={img2_path}"
        )
    img1_path = img1_path[0]
    img2_path = img2_path[0]

    # img1 = cv2.imread(img1_path, cv2.IMREAD_UNCHANGED)
    # img2 = cv2.imread(img2_path, cv2.IMREAD_UNCHANGED)
    img1 = read_by_gdal(img1_path)
    img2 = read_by_gdal(img2_path)

    # 获取水掩膜
    try:
        print('获取水掩膜')
        MSS_input_path = folder_path.replace("_PAN", "_MSS")
        water_mask = get_water_mask(MSS_input_path)
    except Exception as e:
        print('获取水掩膜失败！！！')
        water_mask = np.zeros_like(img1, dtype=np.uint8)

    try:
        print('获取云掩膜')
        cloud_mask = get_cloud_mask(folder_path)
    except Exception as e:
        print('获取云掩膜失败！！！')
        cloud_mask = np.zeros_like(img1, dtype=np.uint8)

    # finalShadow.tif
    try:
        print('获取云影掩膜')
        shadow_mask = get_shadow_mask(folder_path)
        cloud_mask = shadow_mask
    except Exception as e:
        print('获取云影掩膜失败！！！')
        shadow_mask = np.zeros_like(img1, dtype=np.uint8)

    print('============')

    new_height1, new_width1 = img1.shape
    new_height2, new_width2 = img2.shape

    new_height_idx = new_height1 / new_height2
    new_width_idx = new_width1 / new_width2

    img1_resize = cv2.resize(img1, (new_width2, new_height2), interpolation=cv2.INTER_CUBIC)
    del img1
    water_mask_resize = cv2.resize(
        water_mask, (new_width2, new_height2), interpolation=cv2.INTER_NEAREST
    )
    del water_mask
    water_mask_resize[water_mask_resize >= 128] = 255
    water_mask_resize[water_mask_resize < 128] = 0

    try:
        meta_path = glob.glob(os.path.join(folder_path, '*_L1_PAN_meta.xml'))
        if len(meta_path) == 1:
            meta_path = meta_path[0]
            ProductQuality, CloudPercent = read_meta_xml(meta_path)
            # 再次仅对KF02B的云矢量进行扩展，前提是云比例大于50
            if "JL1KF02" in paras["satellite"] and float(CloudPercent) > 50:
                cloud_mask = dilation(cloud_mask, square(25))
                cloud_mask = dilation(cloud_mask, square(25))
    except Exception as e:
        print('从meta.xml获取云量失败！！！')

    # cloud_mask = dilation(cloud_mask, square(25))
    cloud_mask_resize = cv2.resize(
        cloud_mask, (new_width2, new_height2), interpolation=cv2.INTER_NEAREST
    )
    del cloud_mask
    cloud_mask_resize[cloud_mask_resize >= 128] = 255
    cloud_mask_resize[cloud_mask_resize < 128] = 0
    # cloud_mask_resize1 = cv2.resize(cloud_mask, (new_width1, new_height1), interpolation=cv2.INTER_NEAREST)
    # cloud_mask_resize1[cloud_mask_resize1 >= 128] = 255
    # cloud_mask_resize1[cloud_mask_resize1 < 128] = 0
    # cv2.imwrite('cloud.tif', cloud_mask_resize1)
    # return 0, 0, 0

    blur_mask = np.zeros_like(water_mask_resize, dtype=np.uint8)
    block_size = (1024, 1024)
    blocks_water, _, _ = split_image_into_blocks(water_mask_resize, block_size)
    blocks_cloud, _, _ = split_image_into_blocks(cloud_mask_resize, block_size)
    blocks_blur, _, _ = split_image_into_blocks(blur_mask, block_size)
    del water_mask_resize, cloud_mask_resize, blur_mask
    blocks1, positions, indexs = split_image_into_blocks(img1_resize, block_size)
    blocks2, _, _ = split_image_into_blocks(img2, block_size)

    del img1_resize, img2
    datas_sum = np.zeros(len(indexs), dtype=float)
    datas_var = np.zeros(len(indexs), dtype=float)
    datas_index = np.zeros(len(indexs), dtype=int)

    print('第一次过滤')
    parallel_process_byvar(
        indexs, paras, blocks1, blocks2, blocks_water, blocks_cloud,
        datas_sum, datas_var, blocks_blur, datas_index
    )

    isLocalBlur = "NO"
    blur_coordinates = []

    if np.max(datas_sum) < 1:
        print('No blur')
        return {"isLocalBlur": isLocalBlur, "blur_coordinates": blur_coordinates}

    guolv_flag = 0
    if np.max(datas_sum) == 1:
        print('第二次过滤')
        indexs_blur = []
        blocks1_blur = []
        blocks2_blur = []
        datas_sum_blur = []
        datas_var_blur = []
        datas_index_blur = []
        for i in range(len(datas_index)):
            index = datas_index[i]
            data_sum = datas_sum[i]
            idx = indexs.index(index)
            if data_sum == 1:
                blocks1_blur.append(blocks1[idx])
                blocks2_blur.append(blocks2[idx])
                indexs_blur.append(index)
                datas_sum_blur.append(0)
                datas_var_blur.append(0)
                datas_index_blur.append(0)
        parallel_process_byfre(
            indexs_blur, blocks1_blur, blocks2_blur,
            datas_sum_blur, datas_var_blur, datas_index_blur
        )
        # print(np.max(datas_var_blur))
        if np.max(datas_var_blur) < 11000:
            print('No blur')
            return {"isLocalBlur": isLocalBlur, "blur_coordinates": blur_coordinates}
        else:
            guolv_flag = 1
            for i in range(len(datas_index_blur)):
                index = datas_index_blur[i]
                data_var = datas_var_blur[i]
                idx = np.where(datas_index == index)
                if not len(idx) == 1:
                    print('多线程运行错误')
                    return {"isLocalBlur": isLocalBlur, "blur_coordinates": blur_coordinates}
                datas_var[idx] = data_var

    print('存在局部模糊')
    if guolv_flag == 0:
        pici_folder = 'Debug'
        LARGE_REGION_SIZE = paras["threshold_region"]
        res_data = datas_sum
    else:
        pici_folder = 'Debug'
        LARGE_REGION_SIZE = 11000
        res_data = datas_var

    indices = []
    top_indices = res_data.argsort()[-5:][::-1]
    top_values = res_data[top_indices]
    for idx, value in zip(top_indices, top_values):
        if value >= LARGE_REGION_SIZE:
            indices.append(idx)

    outputPath_flag = os.path.join(outputPath, pici_folder)
    os.makedirs(outputPath_flag, exist_ok=True)
    # output = os.path.join(outputPath_flag, os.path.basename(folder_path))
    # os.makedirs(output, exist_ok=True)

    output_bad = os.path.join(outputPath_flag, 'localBlurDetection')
    os.makedirs(output_bad, exist_ok=True)

    binary_image = merge_blocks_to_image(blocks_blur, (new_height2, new_width2), (1024, 1024))
    mask_save_path = os.path.join(output_bad, f'{os.path.basename(folder_path)}_mask.tif')
    resized_bool = resize_bool_matrix(binary_image, (new_width1, new_height1))  # 注意OpenCV尺寸格式为(width, height)
    cv2.imwrite(mask_save_path, resized_bool)
    # 在文件夹下面创建res_all.txt res_worse.txt res_worst.txt
    # res_all = open(os.path.join(output_bad, 'res_all.txt'), 'w', encoding='utf-8')
    # res_bad = open(os.path.join(output_bad, 'res_bad.txt'), 'w', encoding='utf-8')

    for i in range(len(datas_index)):
        index = datas_index[i]
        idx = indexs.index(index)
        pos = positions[idx]
        data_sum = datas_sum[i]
        data_var = datas_var[i]

        idx_str = "{:0>3}".format(index)
        pos1 = (round(pos[0] * new_width_idx), round(pos[1] * new_height_idx))
        str_result = (f"{idx_str}的PAN位置{str(pos1):<15}和NoSR位置{str(pos):<15}处的Blur值为:{data_sum},"
                      f"增强Blur值为{data_var}")
        # res_all.write(str_result + '\n')
        if not i in indices:
            continue

        if res_data[i] >= LARGE_REGION_SIZE:
            block1 = blocks1[idx]
            block2 = blocks2[idx]
            pan_save_path = os.path.join(output_bad, f'{index}_PAN.tif')
            nosr_save_path = os.path.join(output_bad, f'{index}_NOSR.tif')
            cv2.imwrite(pan_save_path, block1)
            cv2.imwrite(nosr_save_path, block2)
            isLocalBlur = "YES"
            str_result = f"{str(pos1)}"
            blur_coordinates.append(str_result)
            # res_bad.write(str_result + '\n')

    # res_all.close()
    # res_bad.close()
    return {"isLocalBlur": isLocalBlur, "blur_coordinates": blur_coordinates}


def modified_meta_xml_quality_AtoB(xml_path, quality_level):
    if not os.path.isfile(xml_path):
        print("请输出正确的xml路径")
        sys.exit(1)
    # 将其写入req.xml
    tree = ET.parse(xml_path)
    root = tree.getroot()
    ProductInfo_root = root.find("ProductInfo")
    ProductQuality_root = ProductInfo_root.find("ProductQuality")
    ProductQuality_root.text = quality_level
    tree.write(xml_path, encoding='utf-8', xml_declaration=True)


def modified_qual_xml_quality_AtoB(xml_path, quality_level):
    if not os.path.isfile(xml_path):
        print("请输出正确的xml路径")
        sys.exit(1)
    # 将其写入req.xml
    tree = ET.parse(xml_path)
    root = tree.getroot()
    IntegratedQuality_root = root.find("IntegratedQuality")
    IntegratedQuality_root.text = quality_level
    tree.write(xml_path, encoding='utf-8', xml_declaration=True)


def modified_req_xml_quality_AtoB(xml_path, quality_level):
    if not os.path.isfile(xml_path):
        print("请输出正确的xml路径")
        sys.exit(1)
    # 将其写入req.xml
    tree = ET.parse(xml_path)
    root = tree.getroot()
    Quality_root = root.find("Quality")
    Quality_root.text = quality_level
    tree.write(xml_path, encoding='utf-8', xml_declaration=True)


def add_modified_quality_AtoB_by_localBlur(inputPath):
    if not os.path.isdir(inputPath):
        print("请输出正确的路径")
        sys.exit(1)

    # [依截图原样保留：此函数读取外层全局 outputPath，而不是 inputPath]
    Debug_path = os.path.join(outputPath, "Debug")
    localBlurDetection_path = os.path.join(Debug_path, "localBlurDetection")
    if not os.path.isdir(localBlurDetection_path):
        print("不存在局部模糊文件夹：...Debug\\localBlurDetection")
        sys.exit(1)
    # 创建标志位
    print(localBlurDetection_path)
    localBlur_flag = open(os.path.join(localBlurDetection_path, 'localBlur_flag_A_B.txt'), 'w', encoding='utf-8')
    localBlur_flag.close()


def get_flag_localBlur(inputPath):
    if not os.path.isdir(inputPath):
        print("请输出正确的路径")
        sys.exit(1)
    # [依截图原样保留：此函数读取外层全局 outputPath，而不是 inputPath]
    Debug_path = os.path.join(outputPath, "Debug")
    localBlurDetection_path = os.path.join(Debug_path, "localBlurDetection")
    if not os.path.isdir(localBlurDetection_path):
        return False
    # 创建标志位
    localBlur_flag = os.path.join(localBlurDetection_path, 'localBlur_flag_A_B.txt')
    if os.path.isfile(localBlur_flag):
        return True
    return False


if __name__ == '__main__':
    # if len(sys.argv) != 2:
    #     print("请输入*.sh 输入路径")
    #     sys.exit(1)
    if len(sys.argv) == 2:
        inputPath = sys.argv[1]

    if len(sys.argv) == 1:
        # 下列为原图中的 Windows 内网调试路径，部分字符受截图分辨率影响可能有出入。
        # inputPath = r'N:\tmp\ytt\blurTest\GF04A_ID\test\JL1GF04A_PMS_20240423132751_200257324_102_0038_001_L1_PAN'
        # inputPath = r'N:\tmp\ytt\blurTest\ChenChaoSystem\ouput\wrong\JL1KF02B02_PMS08_20250509120721_200381389_101_0004_001_L1_PAN'
        inputPath = (r'N:\GSHC2IMPS\PRODUCT\2025\05\14\JL1KF01C'
                     r'\JL1KF01C_PMSL6_20250514102214_200383372_101_001_L1_PAN'
                     r'\JL1KF01C_PMSL6_20250514102214_200383372_101_0005_001_L1_PAN')

    if not os.path.isdir(inputPath):
        print("请输入正确的输入路径")
        sys.exit(1)
    outputPath = inputPath
    # 先判断是否存在req.xml，存在则插入标签<isLocalBlur>和<LocalBlurPosition>
    req_path = glob.glob(os.path.join(inputPath, '*_PAN_req.xml'))
    if len(req_path) != 1:
        print(f'寻找ID:{os.path.basename(inputPath)}的req文件错误')
        sys.exit(1)
    req_path = req_path[0]
    tree = ET.parse(req_path)
    Request = tree.getroot()
    isLocalBlur_root = Request.find("isLocalBlur")
    if isLocalBlur_root is None:
        isLocalBlur_str = "  <isLocalBlur>" + "NULL" + "</isLocalBlur>"
        with open(req_path, "r") as f:
            lines = f.readlines()
        new_lines = []
        for line in lines:
            if line.startswith("</Request>"):
                new_lines.append(isLocalBlur_str + "\n")
            new_lines.append(line)
        with open(req_path, "w") as f:
            f.writelines(new_lines)

    # req_path = glob.glob(os.path.join(inputPath, '*_PAN_req.xml'))
    # if len(req_path) != 1:
    #     print(f'寻找ID:{os.path.basename(inputPath)}的req文件错误')
    #     sys.exit(1)
    meta_path = req_path.replace("_req.xml", "_meta.xml")
    try:
        ProductQuality, CloudPercent = read_meta_xml(meta_path)
    except Exception as e:
        print("获取meta质量等级失败")
        sys.exit(1)

    # 读取图像并预处理
    # img_path = glob.glob(os.path.join(inputPath, '*_NOSR.tif'))
    # if len(img_path) != 1:
    #     print(f'寻找ID:{os.path.basename(inputPath)}的NOSR.tif错误')
    #     sys.exit(1)
    img_path = inputPath

    try:
        if "JL1KF02B" in os.path.basename(img_path):
            results = process_folder_KF(inputPath, outputPath)
        elif "JL1KF01" in os.path.basename(img_path):
            results = process_folder_KF(inputPath, outputPath)
        elif "JL1GF04A_" in os.path.basename(img_path):
            results = process_folder_KF(inputPath, outputPath)

        if results["isLocalBlur"] == "YES":
            blur_coordinates_str = ",".join(results["blur_coordinates"])
            blur_coordinates_str = "," + blur_coordinates_str
        else:
            blur_coordinates_str = ""
        # 将其写入req.xml
        tree = ET.parse(req_path)
        Request = tree.getroot()
        isLocalBlur_root = Request.find("isLocalBlur")
        if isLocalBlur_root is not None:
            isLocalBlur_root.text = results["isLocalBlur"] + blur_coordinates_str
        tree.write(req_path, encoding='utf-8', xml_declaration=True)

        if ProductQuality == "A" and results["isLocalBlur"] == "YES":
            meta_path = req_path.replace("_req.xml", "_meta.xml")
            modified_meta_xml_quality_AtoB(meta_path, "B")
            qual_path = req_path.replace("_req.xml", "_qual.xml")
            modified_qual_xml_quality_AtoB(qual_path, "B")
            modified_req_xml_quality_AtoB(req_path, "B")
            # 添加标志位（由于局部模糊导致质量修改的）
            add_modified_quality_AtoB_by_localBlur(inputPath)

        if ProductQuality == "B" and results["isLocalBlur"] == "NO" and get_flag_localBlur(inputPath):
            meta_path = req_path.replace("_req.xml", "_meta.xml")
            modified_meta_xml_quality_AtoB(meta_path, "A")
            qual_path = req_path.replace("_req.xml", "_qual.xml")
            modified_qual_xml_quality_AtoB(qual_path, "A")
            modified_req_xml_quality_AtoB(req_path, "A")

        print("检测结束")

    except Exception as e:
        print("算法运行错误")
        sys.exit(1)
