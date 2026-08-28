import os
import sys
import time
import math
import torch.nn.functional as F
from datetime import datetime
import random
import logging
from collections import OrderedDict
import numpy as np
import cv2
import torch
from torchvision.utils import make_grid
from shutil import get_terminal_size, rmtree
from xml.dom.minidom import parse
import os.path as osp
from osgeo import gdal
from re import compile, findall
import shutil
import pynvml
import psutil
import subprocess
import re
import yaml
try:
    from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
    from yaml import Loader, Dumper
# from lxml import etree
#
#
def judge_meta(meta_xml_path):
    # parser = etree.XMLParser(remove_comments=False)
    tree = etree.parse(meta_xml_path, parser)
    root = tree.getroot()
    parent_node = root.find("ProcessInfo")
    mtfc_compensation_node = parent_node.find("MtfCompensation")
    if mtfc_compensation_node is None:
        mtfc_compensation_node = etree.Element("MtfCompensation")
        mtfc_compensation_node.text = "NO"
        print("MtfCompensation is writed as NO !!!")
        parent_node.insert(8, mtfc_compensation_node)
        lxml_root = etree.fromstring(etree.tostring(root))
        etree.indent(lxml_root, space="\t")
        declaration = b'<?xml version="1.0" ?>\n'
        new_tree = etree.ElementTree(lxml_root)
        xml_content = etree.tostring(new_tree, encoding="utf-8", pretty_print=True, xml_declaration=False)
        with open(meta_xml_path, "wb") as f:
            f.write(declaration + xml_content)
    else:
        print("MtfCompensation is existed !!!")
        return


def OrderedYaml():
    'yaml orderedDict support'
    _mapping_tag = yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG

    def dict_representer(dumper, data):
        return dumper.represent_dict(data.items())

    def dict_constructor(loader, node):
        return OrderedDict(loader.construct_pairs(node))

    Dumper.add_representer(OrderedDict, dict_representer)
    Loader.add_constructor(_mapping_tag, dict_constructor)
    return Loader, Dumper


# miscellaneous
def get_timestamp():
    return datetime.now().strftime('%y%m%d-%H:%M:%S')


def mkdir(path):
    if not os.path.exists(path):
        os.makedirs(path)


def mkdirs(paths):
    if isinstance(paths, str):
        mkdir(paths)
    else:
        for path in paths:
            mkdir(path)


def mkdir_and_rename(path):
    if os.path.exists(path):
        new_name = path + '_archived_' + get_timestamp()
        print('Path already exists. Rename it to [{:s}]'.format(new_name))
        logger = logging.getLogger('base')
        logger.info('Path already exists. Rename it to [{:s}]'.format(new_name))
        os.rename(path, new_name)
        os.makedirs(path)


def set_random_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def setup_logger(logger_name, level=logging.INFO, screen=False):
    '''set up logger'''
    lg = logging.getLogger(logger_name)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s: %(message)s', datefmt='%y-%m-%d %H:%M:%S')
    lg.setLevel(level)
    if screen:
        sh = logging.StreamHandler()
        sh.setFormatter(formatter)
        lg.addHandler(sh)

def dvd_gp_with_wrap(img_paths, lq_tile_folder_path_list, t_ht, t_wd, pad, tl_info):
    for img_path in img_paths:
        if not osp.exists(img_path):
            continue
        img = osp.basename(img_path)
        if osp.isdir(img_path):
            rmtree(img_path)
            continue
        if not osp.exists(img_path[:-4]):
            os.mkdir(img_path[:-4])
        lq_tile_folder_path_list.append(img_path[:-4])
        dataset = gdal.Open(img_path)
        in_width_gdal = dataset.RasterXSize
        in_height_gdal = dataset.RasterYSize
        image_pan16 = dataset.ReadAsArray(0, 0, in_width_gdal, in_height_gdal)
        max_dn = image_pan16.max()
        min_dn = image_pan16.min()
        # gamma = get_gp_gamma(image_pan16.mean())
        print("Input min %.1f,\t max %.1f,\t mean %.1f,\t" % (min_dn, max_dn, image_pan16.mean()), image_pan16.dtype,
              image_pan16.shape)
        # img_f = (cv2.pow(image_pan16 / 4095, 1 / gamma) * 4095).astype("uint16")  # input data convert 1ch->3ch
        tl_info[img] = {}
        tl_info_temp = tl_info[img]
        tl_info_temp["ori_ht"], tl_info_temp["ori_wd"] = image_pan16.shape
        tl_info_temp["wrap_top"] = (t_ht - tl_info_temp["ori_ht"] % t_ht) // 2
        tl_info_temp["wrap_btm"] = (t_ht - tl_info_temp["ori_ht"] % t_ht) - tl_info_temp["wrap_top"]
        tl_info_temp["wrap_lt"] = (t_wd - tl_info_temp["ori_wd"] % t_wd) // 2
        tl_info_temp["wrap_rt"] = (t_wd - tl_info_temp["ori_wd"] % t_wd) - tl_info_temp["wrap_lt"]
        # tl_info_temp
        tl_info_temp["max_dn"] = max_dn
        tl_info_temp["min_dn"] = min_dn
        img_wrap = np.pad(image_pan16, ((tl_info[img]["wrap_top"], tl_info[img]["wrap_btm"]),
                                        (tl_info[img]["wrap_lt"], tl_info[img]["wrap_rt"])),
                          'constant', constant_values=(25, 25))
        # img_wrap = np.pad(img_f, ((wrap_top, wrap_btm), (wrap_lt, wrap_rt), (0, 0)), 'constant', constant_values=(25, 25))
        # img_wrap_trans = np.transpose(img_wrap, (2, 0, 1))
        split_gp_with_pad(img_wrap, img_path[0:-4], tl_info, t_ht, t_wd, pad, ".tif")
    return lq_tile_folder_path_list, tl_info


def split_gp_with_pad(img_wrap, tile_out_folder, tl_info, t_ht, t_wd, pad, tiftype):
    tl_obj = tl_info[osp.basename(tile_out_folder) + tiftype]
    tile_rows, tile_cols = ((tl_obj["ori_ht"] + tl_obj["wrap_top"] + tl_obj["wrap_btm"]) // t_ht), \
                           ((tl_obj["ori_wd"] + tl_obj["wrap_lt"] + tl_obj["wrap_rt"]) // t_wd)
    tile_arr = np.zeros((tile_rows, tile_cols, t_ht + 2 * pad, t_wd + 2 * pad))
    for i in range(0, tile_rows):
        for j in range(0, tile_cols):
            if i == 0:
                if j == 0:
                    tile_pad = img_wrap[i * t_ht:(i + 1) * t_ht + 2 * pad, j * t_wd:(j + 1) * t_wd + 2 * pad]
                elif j == tile_cols - 1:
                    tile_pad = img_wrap[i * t_ht:(i + 1) * t_ht + 2 * pad, j * t_wd - 2 * pad:(j + 1) * t_wd]
                else:
                    tile_pad = img_wrap[i * t_ht:(i + 1) * t_ht + 2 * pad, j * t_wd - pad:(j + 1) * t_wd + pad]
            elif i == tile_rows - 1:
                if j == 0:
                    tile_pad = img_wrap[i * t_ht - 2 * pad:(i + 1) * t_ht, j * t_wd:(j + 1) * t_wd + 2 * pad]
                elif j == tile_cols - 1:
                    tile_pad = img_wrap[i * t_ht - 2 * pad:(i + 1) * t_ht, j * t_wd - 2 * pad:(j + 1) * t_wd]
                else:
                    tile_pad = img_wrap[i * t_ht - 2 * pad:(i + 1) * t_ht, j * t_wd - pad:(j + 1) * t_wd + pad]
            else:
                if j == 0:
                    tile_pad = img_wrap[i * t_ht - pad:(i + 1) * t_ht + pad, j * t_wd:(j + 1) * t_wd + 2 * pad]
                elif j == tile_cols - 1:
                    tile_pad = img_wrap[i * t_ht - pad:(i + 1) * t_ht + pad, j * t_wd - 2 * pad:(j + 1) * t_wd]
                else:
                    tile_pad = img_wrap[i * t_ht - pad:(i + 1) * t_ht + pad, j * t_wd - pad:(j + 1) * t_wd + pad]
            tile_arr[i, j] = tile_pad
    # img_wrap_trans = np.transpose(img_wrap, (2, 0, 1))  # For debug
    # tile_arr_trans = np.transpose(tile_arr, (0, 1, 4, 2, 3))
    cv2.imwrite(osp.join(tile_out_folder, str(i).zfill(2) + "_" + str(j).zfill(2) + tiftype), tile_pad)


def joint_gp_without_pad(tile_folder, t_ht, t_wd, pad, scl, tl_info, tiftype):
    tl_obj = tl_info[osp.basename(tile_folder) + tiftype]
    tile_rows, tile_cols = ((tl_obj["ori_ht"] + tl_obj["wrap_top"] + tl_obj["wrap_btm"]) // t_ht), \
                           ((tl_obj["ori_wd"] + tl_obj["wrap_lt"] + tl_obj["wrap_rt"]) // t_wd)
    img_sr_wrap = np.zeros(((tl_obj["ori_ht"] + tl_obj["wrap_top"] + tl_obj["wrap_btm"]) * scl,
                            (tl_obj["ori_wd"] + tl_obj["wrap_lt"] + tl_obj["wrap_rt"]) * scl), dtype=np.uint16)
    count = 0
    for folder in sorted(os.listdir(tile_folder)):
        if osp.isdir(osp.join(tile_folder, folder)):
            continue
        sr_frame_name = osp.join(tile_folder, folder)
        sr_tile_pad = cv2.imread(sr_frame_name, cv2.IMREAD_UNCHANGED)
        i, j = count // tile_cols, count % tile_cols
        if i == 0:
            if j == 0:
                img_sr_wrap[0:t_ht * scl, 0:t_wd * scl] = sr_tile_pad[0:-2 * pad * scl, 0:-2 * pad * scl]
            elif j == tile_cols - 1:
                img_sr_wrap[0:t_ht * scl, j * t_wd * scl:(tl_obj["ori_wd"] + tl_obj["wrap_lt"] + tl_obj["wrap_rt"]) * scl] = \
                    sr_tile_pad[0:-2 * pad * scl, 2 * pad * scl:]
            else:
                img_sr_wrap[0:t_ht * scl, (j * t_wd) * scl:(j + 1) * t_wd * scl] = \
                    sr_tile_pad[0:-2 * pad * scl, pad * scl:-pad * scl]
        elif i == tile_rows - 1:
            if j == 0:
                img_sr_wrap[i * t_ht * scl:(tl_obj["ori_ht"] + tl_obj["wrap_top"] + tl_obj["wrap_btm"]) * scl,
                            0:t_wd * scl] = sr_tile_pad[2 * pad * scl:, 0:-2 * pad * scl]
            elif j == tile_cols - 1:
                img_sr_wrap[i * t_ht * scl:(tl_obj["ori_ht"] + tl_obj["wrap_top"] + tl_obj["wrap_btm"]) * scl,
                            j * t_wd * scl:(tl_obj["ori_wd"] + tl_obj["wrap_lt"] + tl_obj["wrap_rt"]) * scl] = \
                    sr_tile_pad[2 * pad * scl:, 2 * pad * scl:]
            else:
                img_sr_wrap[i * t_ht * scl:(tl_obj["ori_ht"] + tl_obj["wrap_top"] + tl_obj["wrap_btm"]) * scl,
                            (j * t_wd) * scl:(j + 1) * t_wd * scl] = \
                    sr_tile_pad[2 * pad * scl:, pad * scl:-pad * scl]
        else:
            if j == 0:
                img_sr_wrap[i * t_ht * scl:(i + 1) * t_ht * scl, 0:t_wd * scl] = \
                    sr_tile_pad[pad * scl:-pad * scl, 0:-2 * pad * scl]
            elif j == tile_cols - 1:
                img_sr_wrap[i * t_ht * scl:(i + 1) * t_ht * scl,
                            j * t_wd * scl:(tl_obj["ori_wd"] + tl_obj["wrap_lt"] + tl_obj["wrap_rt"]) * scl] = \
                    sr_tile_pad[pad * scl:-pad * scl, 2 * pad * scl:]
            else:
                img_sr_wrap[i * t_ht * scl:(i + 1) * t_ht * scl, (j * t_wd) * scl:(j + 1) * t_wd * scl] = \
                    sr_tile_pad[pad * scl:-pad * scl, pad * scl:-pad * scl]
        count += 1
    # img_sr_wrap_trans = np.transpose(img_sr_wrap, (2, 0, 1))
    # writeTiff(img_sr_wrap, '../results/WC4_RRDB_ESRGANx2_GPU0/KF01_GF02/' + folder + "_srx2.tif")
    return img_sr_wrap

def data_split(image_pan16, im_height, im_width, gamma, max_DN=4095):
    image_3ch = np.zeros((3, im_height, im_width), dtype=np.uint16)
    image_pan16 = (np.power(image_pan16 / max_DN, 1 / gamma) * max_DN).astype("uint16")  # gamma correction to restore dark texture
    image_3ch[0, :, :] = image_pan16
    image_3ch[1, :, :] = image_pan16
    image_3ch[2, :, :] = image_pan16
    return image_3ch


def gamma_trans(image, gamma, max_DN):
    print('gamma transfor........')
    image_gamma = (cv2.pow(image / max_DN, 1 / gamma) * max_DN).astype("uint16")
    return image_gamma


def gamma_detrans(image, gamma, max_DN):
    image_gamma = (np.power(image / max_DN, gamma) * max_DN).astype("uint16")
    return image_gamma


def get_folder_list(lq_path, tiftype):
    res_list = []
    for file in os.listdir(lq_path):
        file_abs_path = osp.join(lq_path, file)
        f_exist = osp.exists(file_abs_path)
        f_istif = file.endswith(tiftype)
        f_size = osp.getsize(file_abs_path) / 1024 / 1024 / 1024
        if f_exist and f_istif and f_size < 2:
            res_list.append(osp.join(lq_path, file))
        elif osp.isdir(file_abs_path) and len(os.listdir(file_abs_path)) > 6:
            rmtree(file_abs_path)
    return res_list


def get_order_abs_path(lq_path, tiftype):
    res_list = []
    file_abs_path = osp.join(lq_path, osp.basename(lq_path) + tiftype)
    f_exist = osp.exists(file_abs_path)
    f_size = osp.getsize(file_abs_path) / 1024 / 1024 / 1024
    if f_exist and f_size < 2:
        res_list.append(file_abs_path)
    elif osp.isdir(file_abs_path) and len(os.listdir(file_abs_path)) > 6:
        rmtree(file_abs_path)
    return res_list


def get_gp_tif_list(lq_path, tiftype, flag=False):
    res_list = []
    Bn_pattern = compile(r"(^JL\w+B[3-6].tif)")
    for file in sorted(os.listdir(lq_path)):
        f_istif = file.endswith(tiftype)
        file_abs_path = osp.join(lq_path, file)
        f_exist = osp.exists(file_abs_path)
        # f_size = osp.getsize(file_abs_path) / 1024 / 1024
        if findall(Bn_pattern, file).__len__() != 0 and f_exist and f_istif:
            # if file.endswith("L1_B3.tif") or file.endswith("L1_B4.tif") or file.endswith("L1_B5.tif") or file.endswith("L1_B6.tif"):
            res_list.append(osp.join(lq_path, file))
        elif osp.isdir(file_abs_path) and len(os.listdir(file_abs_path)) > 6:
            rmtree(file_abs_path)
    return sorted(res_list)


def update_opt_dataroot_LQ(opt, list):
    length = len(list)
    if length == 0:
        raise ValueError('Directory of LQ is null.')
    elif length == 1:
        opt["datasets"]["test_"]["datarootLQ"] = list[0]
    elif length > 1:
        opt["datasets"]["test_"]["datarootLQ"] = list[0]
        for i in range(1, length):
            opt["datasets"]["test_" + str(i + 1)]["datarootLQ"] = list[i]


def get_kf_gamma(param):
    return 1.45


def get_gp_gamma(param):
    if param < 500:
        gamma = 1.6
    elif param < 700:
        gamma = 1.4
    elif param < 900:
        gamma = 1.2
    else:
        gamma = 1
    return gamma


def get_ds_band(f_path):
    input_dataset = gdal.Open(f_path)
    input_band = input_dataset.GetRasterBand(1)
    return [input_dataset, input_band]


def renameB3rpc(path):
    for file in os.listdir(path):
        if file.endswith("_B3_rpc.txt"):
            ridx = file.rindex("L1_B3_rpc.txt")
            l1c_rpc_name = file[0:ridx] + "L1C_rpc.txt"
            os.rename(osp.join(path, file), osp.join(path, l1c_rpc_name))
            break


def rename_jpg(path):
    for file in os.listdir(path):
        if "L1_thumb.jpg" in file:
            thumb_ridx = file.rindex("L1_thumb.jpg")
            L1C_thumb_name = file[0:thumb_ridx] + "L1C_thumb.jpg"
            os.rename(osp.join(path, file), osp.join(path, L1C_thumb_name))
        elif "L1.jpg" in file:
            jpg_ridx = file.rindex("L1.jpg")
            jpg_name = file[0:jpg_ridx] + "L1C.jpg"
            os.rename(osp.join(path, file), osp.join(path, jpg_name))


def rename_meta(path):
    for file in os.listdir(path):
        if "L1_meta.xml" in file:
            meta_ridx = file.rindex("L1_meta.xml")
            L1C_meta_name = file[0:meta_ridx] + "L1C_meta.xml"
            os.rename(osp.join(path, file), osp.join(path, L1C_meta_name))
        elif "L1C_meta.xml" in file:
            meta_ridx = file.rindex("L1C_meta.xml")
            L1C_meta_name = file[0:meta_ridx] + "L1C_meta.xml"
            os.rename(osp.join(path, file), osp.join(path, L1C_meta_name))


def merge_3channel(result_3ch):
    image_pan16_sr = result_3ch[0, :, :]
    return image_pan16_sr


def merge_3456channel(channel_list, filetype):
    if len(channel_list) != 4:
        logging.info("B3, B4, B5, B6 not ready in folder {:s}".format(channel_list[0][0:channel_list[0].rindex("/")]))
    in_data = []
    out_abs_path = channel_list[0][0:channel_list[0].rindex("_B3.tif")] + "C" + filetype
    print("L1C product name is:" + osp.basename(out_abs_path))
    for img_path in sorted(channel_list):
        in_data.append(get_ds_band(img_path))
    if len(in_data) != 4:
        logging.info("B3, B4, B5, B6 not ready in folder {:s}".format(channel_list[0][0:channel_list[0].rindex("/")]))
    in_ds1, in_band1 = in_data[0]
    gtiff_driver = gdal.GetDriverByName("GTiff")
    out_ds = gtiff_driver.Create(out_abs_path, in_band1.XSize, in_band1.YSize, 4, in_band1.DataType)
    for i in range(4):
        in_band_data = in_data[i][1].ReadAsArray()
        out_ds.GetRasterBand(i + 1).WriteArray(in_band_data)
    print("Write L1C tif file finished.")

def clear_gp_redundant_file(path):
    Bn_pattern = compile(r"(^JL\w+B\d{1,2}\D+)")
    for file in sorted(os.listdir(path)):
        if osp.isdir(osp.join(path, file)):
            rmtree(osp.join(path, file))
        elif "cloud.TIF" in file:
            os.remove(osp.join(path, file))
        elif "qual.xml" in file:
            os.remove(osp.join(path, file))
        elif ".dbf" in file:
            os.remove(osp.join(path, file))
        elif "prj" in file:
            os.remove(osp.join(path, file))
        elif "shp" in file:
            os.remove(osp.join(path, file))
        elif "shx" in file:
            os.remove(osp.join(path, file))
        elif "_GCP.txt" in file:
            os.remove(osp.join(path, file))
        elif "_MatchPts.txt" in file:
            os.remove(osp.join(path, file))
        elif findall(Bn_pattern, file).__len__() != 0:
            os.remove(osp.join(path, file))


def cut_down_wrap(img, ori_ht, ori_wd, wrap_top, wrap_lt, scl):
    return img[wrap_top * scl:(ori_ht + wrap_top) * scl, wrap_lt * scl:(ori_wd + wrap_lt) * scl]


def remove_folder(path, tl_info, tiftype, t_ht, t_wd):
    tile_num = len(os.listdir(path))
    tl_obj = tl_info[osp.basename(path) + tiftype]
    count_tl_rows = (tl_obj["ori_ht"] + tl_obj["wrap_top"] + tl_obj["wrap_btm"]) // t_ht
    count_tl_cols = (tl_obj["ori_wd"] + tl_obj["wrap_lt"] + tl_obj["wrap_rt"]) // t_wd
    if osp.exists(path) and tile_num == count_tl_rows * count_tl_cols:
        rmtree(path)
    else:
        raise ValueError('Invalid directory to be removed.')


def update_meta(path):
    for f_name in os.listdir(path):
        f_path = osp.join(path, f_name)
        if not f_name.endswith("_meta.xml"):
            continue
        else:
            process_tag(f_path)
            break
    print("Update meta file finished.")


def delete_20_bands(path):
    print("Delete 0-19 single band file.")
    doc_tree = parse(path)
    productInfo = doc_tree.getElementsByTagName("ProductInfo")[0]
    for i in range(20):
        try:
            band_bn_node = doc_tree.getElementsByTagName("BAND_B" + str(i))[0]
        except Exception as e:
            logging.info("BAND_B{:s} not found.".format(str(i)))
    if band_bn_node:
        productInfo.removeChild(band_bn_node)
    with open(osp.join(".", path), "w") as fh:
        doc_tree.writexml(fh)
    del_line(path)


def check_and_add_mtfc_node(path):
    # check and add mtfc node
    doc_tree = parse(path)
    new_node = doc_tree.createElement("MtfCompensation")
    ProcessInfo_node = doc_tree.getElementsByTagName("ProcessInfo")[0]
    subnode = [node for node in ProcessInfo_node.childNodes if node.nodeName == 'MtfCompensation']
    if not subnode:
        new_node_text = doc_tree.createTextNode("NO")
        new_node.appendChild(new_node_text)
        ProcessInfo_node.appendChild(new_node)
        with open(osp.join(".", path), "w") as fh:
            doc_tree.writexml(fh, indent="", addindent="\t")
        print('-----------MtfCompensation has been writed as NO')
    else:
        print('+++++++++++++MtfCompensation is existed +++')
    del_line(path)


def del_line(path):
    with open(path, "r") as f:
        res = f.readlines()
    res = [x for x in res if x.split()]
    with open(path, "w") as f:
        f.write("".join(res))
    f.close()
    return True


def add_product_info(path, start_time, end_time, center_time, rowGSD, colGSD):
    doc_tree = parse(path)
    FUSMethod_node = doc_tree.createElement("FUSMethod")
    FUSMethod_text_value = doc_tree.createTextNode("B-G-R-NIR")
    FUSMethod_node.appendChild(FUSMethod_text_value)
    DataBits_node = doc_tree.createElement("DataBits")
    DataBits_node_text_value = doc_tree.createTextNode("12")
    DataBits_node.appendChild(DataBits_node_text_value)
    StartTime_node = doc_tree.createElement("StartTime")
    start_time_text_value = doc_tree.createTextNode(start_time)
    StartTime_node.appendChild(start_time_text_value)
    EndTime_node = doc_tree.createElement("EndTime")
    end_time_text_value = doc_tree.createTextNode(end_time)
    EndTime_node.appendChild(end_time_text_value)
    CenterTime_node = doc_tree.createElement("CenterTime")
    center_time_text_value = doc_tree.createTextNode(center_time)
    CenterTime_node.appendChild(center_time_text_value)
    ImageRowGSD_node = doc_tree.createElement("ImageRowGSD")
    ImageRowGSD_text_value = doc_tree.createTextNode(str(round((np.float64(rowGSD) * 3 / 5), 2)))
    ImageRowGSD_node.appendChild(ImageRowGSD_text_value)
    ImageColumnGSD_node = doc_tree.createElement("ImageColumnGSD")
    ImageColumnGSD_text_value = doc_tree.createTextNode(str(round((np.float64(colGSD) * 3 / 5), 2)))
    ImageColumnGSD_node.appendChild(ImageColumnGSD_text_value)
    LowerLeftLongitude = doc_tree.getElementsByTagName("ProductInfo")[0]
    LowerLeftLongitude.appendChild(FUSMethod_node)
    LowerLeftLongitude.appendChild(DataBits_node)
    LowerLeftLongitude.appendChild(StartTime_node)
    LowerLeftLongitude.appendChild(EndTime_node)
    LowerLeftLongitude.appendChild(CenterTime_node)
    LowerLeftLongitude.appendChild(ImageRowGSD_node)
    LowerLeftLongitude.appendChild(ImageColumnGSD_node)
    with open(osp.join(".", path), "w") as fh:
        doc_tree.writexml(fh, indent="", addindent="\t")
    del_line(path)


def updat_processinfo(path):
    doc_tree = parse(path)
    ProcessInfo_node = doc_tree.getElementsByTagName("ProcessInfo")[0]
    AbsCalibrationGain_node = doc_tree.getElementsByTagName("AbsCalibrationGain")[0]
    AbsCalibrationBias_node = doc_tree.getElementsByTagName("AbsCalibrationBias")[0]
    AbsCalibrationGain_value = AbsCalibrationGain_node.childNodes[0].data
    AbsCalibrationBias_value = AbsCalibrationBias_node.childNodes[0].data
    ProcessInfo_node.removeChild(AbsCalibrationGain_node)
    ProcessInfo_node.removeChild(AbsCalibrationBias_node)
    AbsGain_list = AbsCalibrationGain_value.split(",")
    AbsBias_list = AbsCalibrationBias_value.split(",")
    AbsGain_value = AbsGain_list[3] + "," + AbsGain_list[4] + "," + AbsGain_list[5] + "," + AbsGain_list[6]
    AbsBias_value = AbsBias_list[3] + "," + AbsBias_list[4] + "," + AbsBias_list[5] + "," + AbsBias_list[6]
    AbsGain_node = doc_tree.createElement("AbsCalibrationGain")
    AbsGain_text_value = doc_tree.createTextNode(AbsGain_value)
    AbsGain_node.appendChild(AbsGain_text_value)
    AbsBias_node = doc_tree.createElement("AbsCalibrationBias")
    AbsBias_text_value = doc_tree.createTextNode(AbsBias_value)
    AbsBias_node.appendChild(AbsBias_text_value)
    # ProcessInfo_node.appendChild(AbsGain_node)
    # ProcessInfo_node.appendChild(AbsBias_node)
    ProcessInfo_node.insertBefore(AbsGain_node, doc_tree.getElementsByTagName("MtfCompensation")[0])
    ProcessInfo_node.insertBefore(AbsBias_node, doc_tree.getElementsByTagName("MtfCompensation")[0])
    with open(osp.join(".", path), "w") as fh:
        doc_tree.writexml(fh, indent="", addindent="\t")
    del_line(path)


def update_tag(f_path, tag_name, value):
    doc_tree = parse(f_path)
    ele = doc_tree.getElementsByTagName(tag_name)[0]
    ele.firstChild.data = value
    with open(osp.join(".", f_path), "w") as fh:
        doc_tree.writexml(fh)
    # def check_and_add_mtfc_node(path):


def update_tag(f_path, tag_name, value):
    doc_tree = parse(f_path)
    ele = doc_tree.getElementsByTagName(tag_name)[0]
    ele.firstChild.data = value
    with open(osp.join(".", f_path), "w") as fh:
        doc_tree.writexml(fh)


def check_and_add_mtfc_node(path):
    # check and add mtfc node
    doc_tree = parse(path)
    new_node = doc_tree.createElement("MtfCompensation")
    ProcessInfo_node = doc_tree.getElementsByTagName("ProcessInfo")[0]
    existing_node = ProcessInfo_node.find(new_node)
    if existing_node is None:
        new_node_text = doc_tree.createTextNode("NO")
        new_node.appendChild(new_node_text)
        ProcessInfo_node.appendChild(new_node)
        print('-----------MtfCompensation has been writed as NO !!!!!!!!')
    else:
        print('+++++++++++++MtfCompensation is existed !!!!!!!!')
    with open(osp.join(".", path), "w") as fh:
        doc_tree.writexml(fh, indent="", addindent="\t")
    del_line(path)


def check_and_add_mtfc_node(path):
    # check and add mtfc node
    doc_tree = parse(path)
    new_node = doc_tree.createElement("MtfCompensation")
    ProcessInfo_node = doc_tree.getElementsByTagName("ProcessInfo")[0]
    subnode = [node for node in ProcessInfo_node.childNodes if node.nodeName == 'MtfCompensation']
    if not subnode:
        new_node_text = doc_tree.createTextNode("NO")
        new_node.appendChild(new_node_text)
        ProcessInfo_node.appendChild(new_node)
        with open(osp.join(".", path), "w") as fh:
            doc_tree.writexml(fh, indent="", addindent="\t")
        print('-----------MtfCompensation has been writed as NO')
    else:
        print('+++++++++++++MtfCompensation is existed +++')
    del_line(path)


def process_tag(path):
    print("Process tags for new meta file.")
    doc_tree = parse(path)
    B3_node = doc_tree.getElementsByTagName("BAND_B3")[0]
    B3_StartTime = B3_node.getElementsByTagName("StartTime")[0].childNodes[0].data
    B3_End_time = B3_node.getElementsByTagName("EndTime")[0].childNodes[0].data
    B3_Center_time = B3_node.getElementsByTagName("CenterTime")[0].childNodes[0].data
    B3_ImageRowGSD = B3_node.getElementsByTagName("ImageRowGSD")[0].childNodes[0].data
    B3_ImageColumnGSD = B3_node.getElementsByTagName("ImageColumnGSD")[0].childNodes[0].data
    delete_20_bands(path)
    add_product_info(path, B3_StartTime, B3_End_time, B3_Center_time, B3_ImageRowGSD, B3_ImageColumnGSD)
    updat_processinfo(path)
    update_tag(path, "Bands", "4")
    update_tag(path, "ProductID", osp.basename(path)[0:-12] + "L1C")

# image convert
def crop_border(img_list, crop_border):
    """Crop borders of images

    Args:
        img_list (list [Numpy]): HWC
        crop_border (int): crop border for each end of height and weight

    Returns:
        (list [Numpy]): cropped image list
    """
    if crop_border == 0:
        return img_list
    else:
        return [v[crop_border:-crop_border, crop_border:-crop_border] for v in img_list]


# image matrix output function
def tensor2img(tensor, out_type, min_max=(0, 1), gamma=1.45, max_DN=4095):
    """
    Converts a torch Tensor into an image Numpy array
    Input: 4D(B, (3/1), H, W), 3D(C, H, W), or 2D(H, W), any range, RGB channel order
    Output: 3D(H, W, C) or 2D(H, W), [0, 255], np.uint8 (default)
    """
    tensor = tensor.squeeze().float().cpu().clamp_(*min_max)  # clamp
    tensor = (tensor - min_max[0]) / (min_max[1] - min_max[0])  # to range [0, 1]
    n_dim = tensor.dim()
    if n_dim == 4:
        n_img = len(tensor)
        img_np = make_grid(tensor, nrow=int(math.sqrt(n_img)), normalize=False).numpy()
        img_np = np.transpose(img_np[[2, 1, 0], :, :], (1, 2, 0))  # HWC, BGR
    elif n_dim == 3:
        img_np = tensor.numpy()
        img_np = np.transpose(img_np[[2, 1, 0], :, :], (1, 2, 0))  # HWC, BGR
    elif n_dim == 2:
        img_np = tensor.numpy()
    else:
        raise TypeError(
            'Only support 4D, 3D and 2D tensor. But received with dimension: {:d}'.format(n_dim))
    if out_type == np.uint8:
        img_np = (img_np * 255.0).round()
        # Important. Unlike matlab, numpy.uint8() WILL NOT round by default.
    elif out_type == np.uint16:
        img_np = cv2.pow(img_np, gamma)
        # print("cv2_pow using.")
        img_np = (img_np * max_DN).round()  # 12bits valid data
    return img_np.astype(out_type)


def tensor2img_fast(tensor, gamma, min, max, max_DN):
    min_max = (0, 1)
    min_dn = np.uint16(np.around((cv2.pow(min / max_DN, 1 / gamma)[0][0]) * max_DN))  # if gamma == 1.45 else min
    max_dn = np.uint16(np.around((cv2.pow(max / max_DN, 1 / gamma)[0][0]) * max_DN))  # if gamma == 1.45 else max
    output = tensor.squeeze()
    output = output.detach().clamp_(min_dn / max_DN, max_dn / max_DN)
    output = ((output - min_max[0]) / (min_max[1] - min_max[0])).cpu().numpy()
    output = cv2.pow(output, gamma)  # if gamma == 1.45 else output
    output = (output * max_DN).round()
    return output.astype(np.uint16)


def save_img(img, img_path, mode='RGB'):
    cv2.imwrite(img_path, img)


def DUF_downsample(x, scale=4):
    """Downsamping with Gaussian kernel used in the DUF official code

    Args:
        x (Tensor, [B, T, C, H, W]): frames to be downsampled.
        scale (int): downsampling factor: 2, 3, 4.
    """
    assert scale in [2, 3, 4], 'Scale [{}] is not supported'.format(scale)

    def gkern(kernlen=13, nsig=1.6):
        import scipy.ndimage.filters as fi
        inp = np.zeros((kernlen, kernlen))
        # set element at the middle to one, a dirac delta
        inp[kernlen // 2, kernlen // 2] = 1
        # gaussian-smooth the dirac, resulting in a gaussian filter mask
        return fi.gaussian_filter(inp, nsig)

    B, T, C, H, W = x.size()
    x = x.view(-1, 1, H, W)
    pad_w, pad_h = 6 + scale * 2, 6 + scale * 2  # 6 is the pad of the gaussian filter
    r_h, r_w = 0, 0
    if scale == 3:
        r_h = 3 - (H % 3)
        r_w = 3 - (W % 3)
    x = F.pad(x, [pad_w, pad_w + r_w, pad_h, pad_h + r_h], 'reflect')
    gaussian_filter = torch.from_numpy(gkern(13, 0.4 * scale)).type_as(x).unsqueeze(0).unsqueeze(0)
    x = F.conv2d(x, gaussian_filter, stride=scale)
    x = x[:, :, 2:-2, 2:-2]
    x = x.view(B, T, C, x.size(2), x.size(3))
    return x


def single_forward(model, inp):
    """PyTorch model forward (single test), it is just a simple wrapper

    Args:
        model (PyTorch model)
        inp (Tensor): inputs defined by the model

    Returns:
        output (Tensor): outputs of the model. float, in CPU
    """
    with torch.no_grad():
        model_output = model(inp)
        if isinstance(model_output, list) or isinstance(model_output, tuple):
            output = model_output[0]
        else:
            output = model_output
    output = output.data.float().cpu()
    return output


def flipx4_forward(model, inp):
    """Flip testing with X4 self ensemble, i.e., normal, flip H, flip W, flip H and W

    Args:
        model (PyTorch model)
        inp (Tensor): inputs defined by the model

    Returns:
        output (Tensor): outputs of the model. float, in CPU
    """
    # normal
    output_f = single_forward(model, inp)
    # flip W
    output = single_forward(model, torch.flip(inp, (-1, )))
    output_f = output_f + torch.flip(output, (-1, ))
    # flip H
    output = single_forward(model, torch.flip(inp, (-2, )))
    output_f = output_f + torch.flip(output, (-2, ))
    # flip both H and W
    output = single_forward(model, torch.flip(inp, (-2, -1)))
    output_f = output_f + torch.flip(output, (-2, -1))
    return output_f / 4


# metric
def ssim(img1, img2):
    C1 = (0.01 * 255) ** 2
    C2 = (0.03 * 255) ** 2
    img1 = img1.astype(np.float64)
    img2 = img2.astype(np.float64)
    kernel = cv2.getGaussianKernel(11, 1.5)
    window = np.outer(kernel, kernel.transpose())
    mu1 = cv2.filter2D(img1, -1, window)[5:-5, 5:-5]  # valid
    mu2 = cv2.filter2D(img2, -1, window)[5:-5, 5:-5]
    mu1_sq = mu1 ** 2
    mu2_sq = mu2 ** 2
    mu1_mu2 = mu1 * mu2
    sigma1_sq = cv2.filter2D(img1 ** 2, -1, window)[5:-5, 5:-5] - mu1_sq
    sigma2_sq = cv2.filter2D(img2 ** 2, -1, window)[5:-5, 5:-5] - mu2_sq
    sigma12 = cv2.filter2D(img1 * img2, -1, window)[5:-5, 5:-5] - mu1_mu2
    ssim_map = ((2 * mu1_mu2 + C1) * (2 * sigma12 + C2)) / ((mu1_sq + mu2_sq + C1) * (sigma1_sq + sigma2_sq + C2))
    return ssim_map.mean()


def calculate_psnr(img1, img2):
    # img1 and img2 have range [0, 255]
    img1 = img1.astype(np.float64)
    img2 = img2.astype(np.float64)
    mse = np.mean((img1 - img2) ** 2)
    if mse == 0:
        return float('inf')
    return 20 * math.log10(255.0 / math.sqrt(mse))


def calculate_ssim(img1, img2):
    '''calculate SSIM
    the same outputs as MATLAB's
    img1, img2: [0, 255]
    '''
    if not img1.shape == img2.shape:
        raise ValueError('Input images must have the same dimensions.')
    if img1.ndim == 2:
        return ssim(img1, img2)
    elif img1.ndim == 3:
        if img1.shape[2] == 3:
            ssims = []
            for i in range(3):
                ssims.append(ssim(img1, img2))
            return np.array(ssims).mean()
        elif img1.shape[2] == 1:
            return ssim(np.squeeze(img1), np.squeeze(img2))
        else:
            raise ValueError('Wrong input image dimensions.')


class ProgressBar(object):
    """A progress bar which can print the progress
    modified from https://github.com/hellock/cvbase/blob/master/cvbase/progress.py
    """

    def __init__(self, task_num=0, bar_width=50, start=True):
        self.task_num = task_num
        max_bar_width = self._get_max_bar_width()
        self.bar_width = (bar_width if bar_width <= max_bar_width else max_bar_width)
        self.completed = 0
        if start:
            self.start()

    def _get_max_bar_width(self):
        terminal_width, _ = get_terminal_size()
        max_bar_width = min(int(terminal_width * 0.6), terminal_width - 50)
        if max_bar_width < 10:
            print('terminal width is too small ({}), please consider widen the terminal for better '
                  'progressbar visualization'.format(terminal_width))
            max_bar_width = 10
        return max_bar_width

    def start(self):
        if self.task_num > 0:
            sys.stdout.write('[{}] 0/{}, elapsed: 0s, ETA:\n{}\n'.format(
                '*' * self.bar_width, self.task_num, 'Start...'))
        else:
            sys.stdout.write('completed: 0, elapsed: 0s')
        sys.stdout.flush()
        self.start_time = time.time()

    def update(self, msg='In progress...'):
        self.completed += 1
        elapsed = time.time() - self.start_time
        # print("elapsed:" + str(elapsed))
        fps = self.completed / (elapsed + 0.001)
        if self.task_num > 0:
            percentage = self.completed / float(self.task_num)
            eta = int(elapsed * (1 - percentage) / percentage + 0.5)
            mark_width = int(self.bar_width * percentage)
            bar_chars = '>' * mark_width + '-' * (self.bar_width - mark_width)
            sys.stdout.write('\033[2F')  # cursor up 2 lines
            sys.stdout.write('\033[J')  # clean the output (remove extra chars since last display)
            sys.stdout.write('[{}] {}/{}, {:.1f} task/s, elapsed: {}s, ETA: {:5}s\n{}\n'.format(
                bar_chars, self.completed, self.task_num, fps, int(elapsed + 0.5), eta, msg))
        else:
            sys.stdout.write('completed: {}, elapsed: {}s, {:.1f} tasks/s'.format(
                self.completed, int(elapsed + 0.5), fps))
        sys.stdout.flush()
def read_img(path):
    gdal.SetConfigOption('GDAL_NUM_THREADS', 'ALL_CPUS')
    gdal.SetConfigOption('GDAL_CACHEMAX', '200')
    dataset = gdal.Open(path)
    in_width_gdal = dataset.RasterXSize  # Raster column number - x
    in_height_gdal = dataset.RasterYSize  # Raster line number - y
    image_pan16 = dataset.ReadAsArray(0, 0, in_width_gdal, in_height_gdal)
    return image_pan16


def read_img_pan(path):
    gdal.SetConfigOption('GDAL_NUM_THREADS', 'ALL_CPUS')
    gdal.SetConfigOption('GDAL_CACHEMAX', '200')
    dataset = gdal.Open(path)
    in_width_gdal = dataset.RasterXSize  # Raster column number - x
    in_height_gdal = dataset.RasterYSize  # Raster line number - y
    image_pan16 = dataset.ReadAsArray(0, 0, in_width_gdal, in_height_gdal)
    return image_pan16


def read_img_cv2(path):
    return cv2.imread(path, cv2.IMREAD_UNCHANGED)


def read_nosr_img(path):
    dataset = gdal.Open(path)
    in_width_gdal = dataset.RasterXSize  # Raster column number - x
    in_height_gdal = dataset.RasterYSize  # Raster line number - y
    image_pan16 = dataset.ReadAsArray(0, 0, in_width_gdal, in_height_gdal)
    return image_pan16


def trans2_uint16_maxdnclip(img, gamma, max):
    return (cv2.pow(img / max, 1 / gamma) * max).astype("uint16")


def pad_reflect(img, top, btm, lt, rt):
    return np.pad(img, ((top, btm), (lt, rt)), 'reflect')


def dvd_2_grid(img_paths, pad, t_ht, t_wd, tiftype, ori_shape):
    # global im_geotrans, im_proj
    tl_info = {}
    for img_path in img_paths:
        if not osp.exists(img_path):
            continue
        img = osp.basename(img_path)
        if osp.isdir(img_path):
            shutil.rmtree(img_path)
            continue
        # dataset = gdal.Open(img_path)
        # in_width_gdal = dataset.RasterXSize  # Raster column number - x
        # in_height_gdal = dataset.RasterYSize  # Raster line number - y
        # image_pan16 = dataset.ReadAsArray(0, 0, in_width_gdal, in_height_gdal)
        # gamma = get_gamma(image_pan16, gamma_dynamic_valid)
        # print("Input min %.1f, max %.1f, mean %.1f, gamma %.2f," % (image_pan16.min(), image_pan16.max(), image_pan16.mean(), gamma))
        # img_f = (cv2.pow(image_pan16 / max_DN, 1 / gamma) * max_DN).astype("uint16")
        # im_geotrans = dataset.GetGeoTransform()
        # im_proj = dataset.GetProjection()
        tl_info[img] = {}
        tl_info_temp = tl_info[img]
        tl_info_temp["ori_ht"], tl_info_temp["ori_wd"] = ori_shape
        tl_info_temp["wrap_top"] = (t_ht - tl_info_temp["ori_ht"] % t_ht) // 2
        tl_info_temp["wrap_btm"] = (t_ht - tl_info_temp["ori_ht"] % t_ht) - tl_info_temp["wrap_top"]
        tl_info_temp["wrap_lt"] = (t_wd - tl_info_temp["ori_wd"] % t_wd) // 2
        tl_info_temp["wrap_rt"] = (t_wd - tl_info_temp["ori_wd"] % t_wd) - tl_info_temp["wrap_lt"]
        # img_wrap = np.pad(img_f, ((tl_info[img]["wrap_top"], tl_info[img]["wrap_btm"]),
        #                           (tl_info[img]["wrap_lt"], tl_info[img]["wrap_rt"]), (0, 0)), 'constant', constant_values=(25, 25))
        # img_wrap_trans = np.transpose(img_wrap, (2, 0, 1))
        tile_grid_dict = calc_grid_list(img_path[0:-4], tl_info, t_ht, t_wd, pad, tiftype)
    return tile_grid_dict, tl_info


def get_gamma(arr, gamma_dynamic_valid=False):
    if gamma_dynamic_valid:
        if arr.mean() < 500:
            gamma = 1.6
        elif arr.mean() < 700:
            gamma = 1.4
        elif arr.mean() < 900:
            gamma = 1.2
        else:
            gamma = 1
    else:
        gamma = 1
    return gamma


def calc_grid_list(path, tl_info, t_ht, t_wd, pad, filetype):
    tl_obj = tl_info[osp.basename(path) + filetype]
    tile_rows = (tl_obj["ori_ht"] + tl_obj["wrap_top"] + tl_obj["wrap_btm"]) // t_ht
    tile_cols = (tl_obj["ori_wd"] + tl_obj["wrap_lt"] + tl_obj["wrap_rt"]) // t_wd
    res_list = {}
    for i in range(0, tile_rows):
        for j in range(0, tile_cols):
            key = str(i).zfill(2) + "_" + str(j).zfill(2)
            if i == 0:
                if j == 0:
                    res_list[key] = [i * t_ht, (i + 1) * t_ht + 2 * pad, j * t_wd, (j + 1) * t_wd + 2 * pad]
                    # print(res_list[key], "1")
                elif j == tile_cols - 1:
                    res_list[key] = [i * t_ht, (i + 1) * t_ht + 2 * pad, j * t_wd - 2 * pad, (j + 1) * t_wd]
                    # print(res_list[key], "2")
                else:
                    res_list[key] = [i * t_ht, (i + 1) * t_ht + 2 * pad, j * t_wd - pad, (j + 1) * t_wd + pad]
                    # print(res_list[key], "3")
            elif i == tile_rows - 1:
                if j == 0:
                    res_list[key] = [i * t_ht - 2 * pad, (i + 1) * t_ht, j * t_wd, (j + 1) * t_wd + 2 * pad]
                    # print(res_list[key], "4")
                elif j == tile_cols - 1:
                    res_list[key] = [i * t_ht - 2 * pad, (i + 1) * t_ht, j * t_wd - 2 * pad, (j + 1) * t_wd]
                    # print(res_list[key], "5")
                else:
                    res_list[key] = [i * t_ht - 2 * pad, (i + 1) * t_ht, j * t_wd - pad, (j + 1) * t_wd + pad]
                    # print(res_list[key], "6")
            else:
                if j == 0:
                    res_list[key] = [i * t_ht - pad, (i + 1) * t_ht + pad, j * t_wd, (j + 1) * t_wd + 2 * pad]
                    # print(res_list[key], "7")
                elif j == tile_cols - 1:
                    res_list[key] = [i * t_ht - pad, (i + 1) * t_ht + pad, j * t_wd - 2 * pad, (j + 1) * t_wd]
                    # print(res_list[key], "8")
                else:
                    res_list[key] = [i * t_ht - pad, (i + 1) * t_ht + pad, j * t_wd - pad, (j + 1) * t_wd + pad]
                    # print(res_list[key], "9")
            # input()
    return res_list


def get_l1_pan_tif_rcsc(lq_path, file_type, previous_step):
    rc_file_abs_path = osp.join(lq_path, "PAN.tif")
    sc_file_abs_path = osp.join(lq_path, osp.basename(lq_path) + file_type)
    if previous_step == "RC":
        if osp.exists(rc_file_abs_path):
            f_size_GB = osp.getsize(rc_file_abs_path) / 1024 / 1024 / 1024  # GB size
            if 1.0 < f_size_GB < 1.8:
                return rc_file_abs_path
            else:
                print(lq_path + " already SRed before or the previous step was WRONG!")
                exit()
        else:
            print(rc_file_abs_path + " not existed")
            exit()
    else:
        if osp.exists(sc_file_abs_path):
            f_size_GB = osp.getsize(sc_file_abs_path) / 1024 / 1024 / 1024  # GB size
            if 0.0 < f_size_GB < 1.1 or 1.5 < f_size_GB < 1.7 or 3.8 < f_size_GB < 4.1:
                return sc_file_abs_path
            else:
                print(lq_path + " already SRed before")  # huai
                return sc_file_abs_path
                # exit()
        else:
            print(sc_file_abs_path + " not existed")
            exit()


def get_pan_tif(lq_path):
    file_abs_path = osp.join(lq_path, "PAN.tif")
    print(file_abs_path)
    return file_abs_path


def get_l1_pan_tif_rcsc_nosr(lq_path, file_type, previous_step):
    rc_file_abs_path = osp.join(lq_path, "PAN.tif")
    sc_file_abs_path = osp.join(lq_path, osp.basename(lq_path) + '_NOSR' + file_type)
    if previous_step == "RC":
        if osp.exists(rc_file_abs_path):
            f_size_GB = osp.getsize(rc_file_abs_path) / 1024 / 1024 / 1024  # GB size
            if f_size_GB < 1.8:
                return rc_file_abs_path
            else:
                print(lq_path + " already SRed before")
                exit()
        else:
            print(rc_file_abs_path + " not existed")
            exit()
    else:
        if osp.exists(sc_file_abs_path):
            f_size_GB = osp.getsize(sc_file_abs_path) / 1024 / 1024 / 1024  # GB size
            if 0.2 < f_size_GB < 1.1 or 1.5 < f_size_GB < 1.7 or 3.8 < f_size_GB < 4.3:
                return sc_file_abs_path
            else:
                print(lq_path + " already SRed before")
                exit()
        else:
            print(sc_file_abs_path + " not existed")
            exit()


# by fanhaiyang
def get_l1_pan_tif(lq_path, file_type):
    for file in os.listdir(lq_path):
        file_abs_path = osp.join(lq_path, file)
        f_exist = osp.exists(file_abs_path)
        if not f_exist:
            continue
        f_size_GB = osp.getsize(file_abs_path) / 1024 / 1024 / 1024  # GB size
        if osp.basename(lq_path).startswith("JL1GF04A"):
            f_is_pan_tif = file.endswith("PAN" + file_type) & file.startswith("PAN" + file_type)
            if f_is_pan_tif and f_size_GB < 2:
                return file_abs_path
        else:
            f_is_pan_tif = file.endswith("L1_PAN" + file_type)
            if not f_is_pan_tif:
                continue
            pattern_pms = compile(r"^JL\d+\w+_PMS(\w{2})\w*_PAN.tif$")
            pms = re.findall(pattern_pms, file)[0] if len(re.findall(pattern_pms, file)) != 0 else None
            if pms is None:
                continue
            if (pms[0].startswith("0") or pms[0].startswith("R")) and f_size_GB < 1:
                return file_abs_path
            if (pms[0] == "0" or pms[0] == "1") and f_size_GB < 2 and f_size_GB > 0.4:
                return file_abs_path


# by wfp
def get_pan_tif(lq_path, file_type):
    for file in os.listdir(lq_path):
        file_abs_path = osp.join(lq_path, file)
        f_exist = osp.exists(file_abs_path)
        if not f_exist:
            continue
        f_size_GB = osp.getsize(file_abs_path) / 1024 / 1024 / 1024  # GB size
        if osp.basename(lq_path).startswith("JL1KF02A"):
            f_is_pan_tif = file.endswith("PAN" + file_type) & file.startswith("P" + file_type)
            if f_is_pan_tif and f_size_GB < 2:
                return file_abs_path


def get_tif(lq_path, file_type):
    for file in os.listdir(lq_path):
        file_abs_path = osp.join(lq_path, file)
        f_exist = osp.exists(file_abs_path)
        if not f_exist:
            continue
        return file_abs_path


def get_cfg_value(xml_config_file_path, tag, date=None):
    if osp.exists(xml_config_file_path):
        # with open(xml_config_file_path, "r", encoding="utf-8") as f:
        #     print(f.read())
        document_tree = parse(xml_config_file_path)
    else:
        print("xml order file not exist.")
        exit()
    if date is None:
        input_folder_path = document_tree.getElementsByTagName(tag)[0]
        if input_folder_path.childNodes.length != 0:
            value = input_folder_path.childNodes[0].data
            return value
        else:
            return None
    else:
        params = document_tree.getElementsByTagName("param")
        for param in params:
            date_tag = param.getElementsByTagName("date")[0]
            date_value = date_tag.childNodes[0].data
            if date_value.split("-")[0] <= date < date_value.split("-")[1]:
                tag = param.getElementsByTagName(tag)[0]
                value = tag.childNodes[0].data
        return value
def init_res_img(tl_obj, scl):
    ht = (tl_obj["ori_ht"] + tl_obj["wrap_top"] + tl_obj["wrap_btm"]) * scl
    wd = (tl_obj["ori_wd"] + tl_obj["wrap_lt"] + tl_obj["wrap_rt"]) * scl
    return np.zeros((ht, wd), dtype=np.uint16)


def init_res_img_huai(tl_obj, scl):
    ht = (tl_obj["ori_ht"] + tl_obj["wrap_top"] + tl_obj["wrap_btm"]) * scl
    wd = (tl_obj["ori_wd"] + tl_obj["wrap_lt"] + tl_obj["wrap_rt"]) * scl
    return np.zeros((3, ht, wd), dtype=np.uint16)


def get_LQ_tile(array, max_DN):
    img = array.astype(np.float32) / max_DN  # 12bits valid data
    if img.ndim == 2:
        img = np.expand_dims(img, axis=2)
    # some images have 4 channels
    if img.shape[2] > 3:
        img = img[:, :, :3]
    img_LQ_tensor = torch.from_numpy(np.ascontiguousarray(np.transpose(img, (2, 0, 1)))).float().unsqueeze(dim=0)
    return img_LQ_tensor


def get_LQ_tile_huai(array, max_DN):
    img = array.astype(np.float32) / max_DN  # 12bits valid data
    # if img.ndim == 2:
    #     img = np.expand_dims(img, axis=2)
    # some images have 4 channels
    # if img.shape[2] > 3:
    #     img = img[:, :, :3]
    # img_LQ_tensor = torch.from_numpy(np.ascontiguousarray(np.transpose(img, (2, 0, 1)))).float().unsqueeze(dim=0)
    img_LQ_tensor = torch.from_numpy(np.ascontiguousarray(img)).float().unsqueeze(dim=0)
    return img_LQ_tensor


def get_LQ_tile_3ch(array, max_DN):
    img = array.astype(np.float32) / max_DN  # 12bits valid data
    img = img[np.newaxis, :, :, ]
    img = img.repeat([3], axis=0)
    img_LQ_tensor = torch.from_numpy(np.ascontiguousarray(img)).float().unsqueeze(dim=0)
    return img_LQ_tensor


def get_LQ_tile_3(array, max_DN):
    img = array.astype(np.float32) / max_DN  # 12bits valid data
    img = img[np.newaxis, :, :, ]
    img = img.repeat([3], axis=0)
    img_LQ_tensor = torch.from_numpy(np.ascontiguousarray(img))
    return img_LQ_tensor


def get_LQ_tile_ori(array, max_DN):
    if np.max(array) > 256:
        img = array.astype(np.float32) / max_DN  # 12bits valid data
    elif np.max(array) < 256:
        img = array.astype(np.float32) / 255.  # image input to float32
    else:
        img = array.astype(np.float32) / 255.  # image input to float32
    if img.ndim == 2:
        img = np.expand_dims(img, axis=2)
    # some images have 4 channels
    if img.shape[2] > 3:
        img = img[:, :, :3]
    img_LQ_tensor = torch.from_numpy(np.ascontiguousarray(np.transpose(img, (2, 0, 1)))).float().unsqueeze(dim=0)
    return img_LQ_tensor


def fill_sr_grid(key, img, img_sr_wrap, tile_rows, tile_cols, tl_info, name, pad, scl, t_ht, t_wd):
    tl_obj = tl_info[name]
    # img_sr_wrap = np.zeros(((tl_obj["ori_ht"] + tl_obj["wrap_top"] + tl_obj["wrap_btm"]) * scl,
    #                         (tl_obj["ori_wd"] + tl_obj["wrap_lt"] + tl_obj["wrap_rt"]) * scl), dtype=np.uint16)
    i, j = int(key.split("_")[0]), int(key.split("_")[1])
    if i == 0:
        if j == 0:
            img_sr_wrap[0:t_ht * scl, 0:t_wd * scl] = img[0:-2 * pad * scl, 0:-2 * pad * scl]
        elif j == tile_cols - 1:
            img_sr_wrap[0:t_ht * scl, j * t_wd * scl:(tl_obj["ori_wd"] + tl_obj["wrap_lt"] + tl_obj["wrap_rt"]) * scl] = \
                img[0:-2 * pad * scl, 2 * pad * scl:]
        else:
            img_sr_wrap[0:t_ht * scl, (j * t_wd) * scl:(j + 1) * t_wd * scl] = \
                img[0:-2 * pad * scl, pad * scl:-pad * scl]
    elif i == tile_rows - 1:
        if j == 0:
            img_sr_wrap[i * t_ht * scl:(tl_obj["ori_ht"] + tl_obj["wrap_top"] + tl_obj["wrap_btm"]) * scl,
                        0:t_wd * scl] = img[2 * pad * scl:, 0:-2 * pad * scl]
        elif j == tile_cols - 1:
            img_sr_wrap[i * t_ht * scl:(tl_obj["ori_ht"] + tl_obj["wrap_top"] + tl_obj["wrap_btm"]) * scl,
                        j * t_wd * scl:(tl_obj["ori_wd"] + tl_obj["wrap_lt"] + tl_obj["wrap_rt"]) * scl] = \
                img[2 * pad * scl:, 2 * pad * scl:]
        else:
            img_sr_wrap[i * t_ht * scl:(tl_obj["ori_ht"] + tl_obj["wrap_top"] + tl_obj["wrap_btm"]) * scl,
                        (j * t_wd) * scl:(j + 1) * t_wd * scl] = \
                img[2 * pad * scl:, pad * scl:-pad * scl]
    else:
        if j == 0:
            img_sr_wrap[(i * t_ht) * scl:((i + 1) * t_ht) * scl, 0:t_wd * scl] = \
                img[pad * scl:-pad * scl, 0:-2 * pad * scl]
        elif j == tile_cols - 1:
            img_sr_wrap[(i * t_ht) * scl:((i + 1) * t_ht) * scl,
                        j * t_wd * scl:(tl_obj["ori_wd"] + tl_obj["wrap_lt"] + tl_obj["wrap_rt"]) * scl] = \
                img[pad * scl:-pad * scl, 2 * pad * scl:]
        else:
            img_sr_wrap[(i * t_ht) * scl:((i + 1) * t_ht) * scl,
                        (j * t_wd) * scl:(j + 1) * t_wd * scl] = \
                img[pad * scl:-pad * scl, pad * scl:-pad * scl]


def fill_sr_grid_huai(key, img, img_sr_wrap, tile_rows, tile_cols, tl_info, name, pad, scl, t_ht, t_wd):
    tl_obj = tl_info[name]
    # img_sr_wrap = np.zeros(((tl_obj["ori_ht"] + tl_obj["wrap_top"] + tl_obj["wrap_btm"]) * scl,
    #                         (tl_obj["ori_wd"] + tl_obj["wrap_lt"] + tl_obj["wrap_rt"]) * scl), dtype=np.uint16)
    i, j = int(key.split("_")[0]), int(key.split("_")[1])
    if i == 0:
        if j == 0:
            img_sr_wrap[:, 0:t_ht * scl, 0:t_wd * scl] = img[:, 0:-2 * pad * scl, 0:-2 * pad * scl]
        elif j == tile_cols - 1:
            img_sr_wrap[:, 0:t_ht * scl, j * t_wd * scl:(tl_obj["ori_wd"] + tl_obj["wrap_lt"] + tl_obj["wrap_rt"]) * scl] = \
                img[:, 0:-2 * pad * scl, 2 * pad * scl:]
        else:
            img_sr_wrap[:, 0:t_ht * scl, (j * t_wd) * scl:(j + 1) * t_wd * scl] = \
                img[:, 0:-2 * pad * scl, pad * scl:-pad * scl]
    elif i == tile_rows - 1:
        if j == 0:
            img_sr_wrap[:, i * t_ht * scl:(tl_obj["ori_ht"] + tl_obj["wrap_top"] + tl_obj["wrap_btm"]) * scl,
                        0:t_wd * scl] = img[:, 2 * pad * scl:, 0:-2 * pad * scl]
        elif j == tile_cols - 1:
            img_sr_wrap[:, i * t_ht * scl:(tl_obj["ori_ht"] + tl_obj["wrap_top"] + tl_obj["wrap_btm"]) * scl,
                        j * t_wd * scl:(tl_obj["ori_wd"] + tl_obj["wrap_lt"] + tl_obj["wrap_rt"]) * scl] = \
                img[:, 2 * pad * scl:, 2 * pad * scl:]
        else:
            img_sr_wrap[:, i * t_ht * scl:(tl_obj["ori_ht"] + tl_obj["wrap_top"] + tl_obj["wrap_btm"]) * scl,
                        (j * t_wd) * scl:(j + 1) * t_wd * scl] = \
                img[:, 2 * pad * scl:, pad * scl:-pad * scl]
    else:
        if j == 0:
            img_sr_wrap[:, (i * t_ht) * scl:((i + 1) * t_ht) * scl, 0:t_wd * scl] = \
                img[:, pad * scl:-pad * scl, 0:-2 * pad * scl]
        elif j == tile_cols - 1:
            img_sr_wrap[:, (i * t_ht) * scl:((i + 1) * t_ht) * scl,
                        j * t_wd * scl:(tl_obj["ori_wd"] + tl_obj["wrap_lt"] + tl_obj["wrap_rt"]) * scl] = \
                img[:, pad * scl:-pad * scl, 2 * pad * scl:]
        else:
            img_sr_wrap[:, (i * t_ht) * scl:((i + 1) * t_ht) * scl,
                        (j * t_wd) * scl:(j + 1) * t_wd * scl] = \
                img[:, pad * scl:-pad * scl, pad * scl:-pad * scl]


def is_true(string):
    if string.strip().upper() == "TRUE":
        return True
    else:
        return False


def is_false(string):
    if string.strip().upper() == "FALSE":
        return True
    else:
        return False
def writeTiff(im_data, path, tiftype, del_ori_tif=False):
    if osp.exists(osp.join(path[0: path.rindex("/")], "Debug", path[path.rindex("/") + 1:] + "_SRLOG.txt")):
        with open(osp.join(path[0: path.rindex("/")], "Debug", path[path.rindex("/") + 1:] + "_SRLOG.txt"), 'a') as srlog:
            srlog.writelines("\nwriteTiff()\n" + path + ".tif")
    print("writeTiff()" + path)
    if len(im_data.shape) == 3:
        im_height, im_width, im_bands = np.shape(im_data)
    else:
        im_bands, (im_height, im_width) = 1, im_data.shape
    if 'int8' in im_data.dtype.name:
        datatype = gdal.GDT_Byte
    elif 'int16' in im_data.dtype.name:
        datatype = gdal.GDT_UInt16
    elif 'uint16' in im_data.dtype.name:
        datatype = gdal.GDT_UInt16
    else:
        datatype = gdal.GDT_UInt16
    driver = gdal.GetDriverByName("GTiff")
    if is_true(del_ori_tif):
        if osp.exists(path + tiftype) and osp.getsize(path + tiftype) / 1024 / 1024 / 1024 < 2:
            os.remove(path + tiftype)
        dataset = driver.Create(path + tiftype, im_width, im_height, im_bands, datatype)
    else:
        if osp.basename(path).startswith("PAN") and osp.basename(path).endswith("PAN"):  # GF04A will sr PAN.tif file
            try:
                os.rename(path + tiftype, path + "_ori" + tiftype)
            except FileNotFoundError as e:
                print(e)
        else:
            try:
                os.rename(path + tiftype, path + "_NOSR" + tiftype)
            except FileNotFoundError as e:
                print(e)
        dataset = driver.Create(path + tiftype, im_width, im_height, im_bands, datatype)
    if im_bands == 1:
        dataset.GetRasterBand(1).WriteArray(im_data)
    else:
        for i in range(im_bands):
            dataset.GetRasterBand(i + 1).WriteArray(im_data[:, :, i])
    del dataset


def writeTiff_huai(im_data, path, tiftype, del_ori_tif=False):
    if osp.exists(osp.join(path[0: path.rindex("/")], "Debug", path[path.rindex("/") + 1:] + "_SRLOG.txt")):
        with open(osp.join(path[0: path.rindex("/")], "Debug", path[path.rindex("/") + 1:] + "_SRLOG.txt"), 'a') as srlog:
            srlog.writelines("\nwriteTiff()\n" + path + ".tif")
    print("writeTiff()" + path)
    if len(im_data.shape) == 3:
        # im_height, im_width, im_bands = np.shape(im_data)
        im_bands, im_height, im_width = np.shape(im_data)
    else:
        im_bands, (im_height, im_width) = 1, im_data.shape
    if 'int8' in im_data.dtype.name:
        datatype = gdal.GDT_Byte
    elif 'int16' in im_data.dtype.name:
        datatype = gdal.GDT_UInt16
    elif 'uint16' in im_data.dtype.name:
        datatype = gdal.GDT_UInt16
    else:
        datatype = gdal.GDT_UInt16
    driver = gdal.GetDriverByName("GTiff")
    if is_true(del_ori_tif):
        if osp.exists(path + tiftype) and osp.getsize(path + tiftype) / 1024 / 1024 / 1024 < 2:
            os.remove(path + tiftype)
        dataset = driver.Create(path + tiftype, im_width, im_height, im_bands, datatype)
    else:
        if osp.basename(path).startswith("PAN") and osp.basename(path).endswith("PAN"):  # GF04A will sr PAN.tif file
            try:
                os.rename(path + tiftype, path + "_ori" + tiftype)
            except FileNotFoundError as e:
                print(e)
        else:
            try:
                os.rename(path + tiftype, path + "_NOSR" + tiftype)
            except FileNotFoundError as e:
                print(e)
        dataset = driver.Create(path + tiftype, im_width, im_height, im_bands, datatype)
    if im_bands == 1:
        dataset.GetRasterBand(1).WriteArray(im_data)
    else:
        for i in range(im_bands):
            # dataset.GetRasterBand(i + 1).WriteArray(im_data[:, :, i])
            dataset.GetRasterBand(i + 1).WriteArray(im_data[1, :, :])
    del dataset


def writeTiff_Res(im_data, path, tiftype, del_ori_tif=False):
    if osp.exists(osp.join(path[0: path.rindex("/")], "Debug", path[path.rindex("/") + 1:] + "_SRLOG.txt")):
        with open(osp.join(path[0: path.rindex("/")], "Debug", path[path.rindex("/") + 1:] + "_SRLOG.txt"), 'a') as srlog:
            srlog.writelines("\nwriteTiff()\n" + path + ".tif")
    print("writeTiff()" + path)
    if len(im_data.shape) == 3:
        im_height, im_width, im_bands = np.shape(im_data)
    else:
        im_bands, (im_height, im_width) = 1, im_data.shape
    if 'int8' in im_data.dtype.name:
        datatype = gdal.GDT_Byte
    elif 'int16' in im_data.dtype.name:
        datatype = gdal.GDT_UInt16
    elif 'uint16' in im_data.dtype.name:
        datatype = gdal.GDT_UInt16
    else:
        datatype = gdal.GDT_UInt16
    driver = gdal.GetDriverByName("GTiff")
    if is_true(del_ori_tif):
        if osp.exists(path + tiftype) and osp.getsize(path + tiftype) / 1024 / 1024 / 1024 < 2:
            os.remove(path + tiftype)
        dataset = driver.Create(path + tiftype, im_width, im_height, im_bands, datatype)
    else:
        if osp.basename(path).startswith("PAN") and osp.basename(path).endswith("PAN"):  # GF04A will sr PAN.tif file
            try:
                os.rename(path + tiftype, path + "_ori" + tiftype)
            except FileNotFoundError as e:
                print(e)
        else:
            try:
                os.remove(path + tiftype)
            except FileNotFoundError as e:
                print(e)
        dataset = driver.Create(path + tiftype, im_width, im_height, im_bands, datatype)
    if im_bands == 1:
        dataset.GetRasterBand(1).WriteArray(im_data)
    else:
        for i in range(im_bands):
            dataset.GetRasterBand(i + 1).WriteArray(im_data[:, :, i])
    del dataset


def writeTiff_fast(im_data, path, tiftype):
    if len(im_data.shape) == 3:
        im_height, im_width, im_bands = np.shape(im_data)
    else:
        im_bands, (im_height, im_width) = 1, im_data.shape
    if 'int8' in im_data.dtype.name:
        datatype = gdal.GDT_Byte
    elif 'int16' in im_data.dtype.name:
        datatype = gdal.GDT_UInt16
    elif 'uint16' in im_data.dtype.name:
        datatype = gdal.GDT_UInt16
    else:
        datatype = gdal.GDT_UInt16
    driver = gdal.GetDriverByName("GTiff")
    os.rename(path + tiftype, path + "_NOSR" + tiftype)
    dataset = driver.Create(path + tiftype, im_width, im_height, im_bands, datatype)
    if im_bands == 1:
        dataset.GetRasterBand(1).WriteArray(im_data)
    else:
        for i in range(im_bands):
            dataset.GetRasterBand(i + 1).WriteArray(im_data[:, :, i])
    del dataset


def pt_process(key, tl_obj, gpuid, t_ht, t_wd, logpath, gamma, row_step=4):
    tile_rows = ((tl_obj["ori_ht"] + tl_obj["wrap_top"] + tl_obj["wrap_btm"]) // t_ht)
    tile_cols = ((tl_obj["ori_wd"] + tl_obj["wrap_lt"] + tl_obj["wrap_rt"]) // t_wd)
    row, col = key.split("_")
    if int(col) == tile_cols - 1:
        if int(row) % row_step == 0 or int(row) == tile_rows - 1:
            if gamma == 1:
                print("{}_... row sr well done.[GPU {:s}] {:s}".format(str(row), str(gpuid), get_timestamp()))
                # with open(logpath, 'a') as srlog:
                #     srlog.writelines("\n    " + "{}...row sr well done.[GPU {:s}] {:s}".format(str(row), str(gpuid), get_timestamp()))
            else:
                print("{}_... row sr well done after histo match(gamma={:s}).[GPU {:s}] {:s}".format(str(row), str(gamma), str(gpuid), get_timestamp()))
                # with open(logpath, 'a') as srlog:
                #     srlog.writelines("\n    " + "{}...row sr well done after histo match(gamma={:s}).[GPU {:s}] {:s}".format(str(row), str(gamma), str(gpuid), get_timestamp()))


def get_img_date(name):
    pattern1 = compile(r"JL\d+\w+_PMS\d*_(\d*)_\w+_PAN.tif$")
    pattern2 = compile(r"JL\d+\w+_PMS\w\d*_(\d*)_\w+_PAN.tif$")
    pattern3 = compile(r"JL\d+\w+_PMS\d*_(\d*)_\w+_PAN_meta.xml$")
    pattern4 = compile(r"JL\d+\w+_PMS\w\d*_(\d*)_\w+_PAN_meta.xml$")
    if findall(pattern1, name).__len__() != 0:
        date_value = findall(pattern1, name)[0]
        return date_value
    elif findall(pattern2, name).__len__() != 0:
        date_value = findall(pattern2, name)[0]
        return date_value
    elif findall(pattern3, name).__len__() != 0:
        date_value = findall(pattern3, name)[0]
        return date_value
    elif findall(pattern4, name).__len__() != 0:
        date_value = findall(pattern4, name)[0]
        return date_value


def resize_by_sat(img, name):
    if name.startswith("JL1KF01A"):
        if get_img_date(name) < "20211205":  # before orbit, return 0.5
            return img
        else:
            print("resize_by_sat JL1KF01A")
            return cv2.resize(img, None, fx=1.1, fy=1.1, interpolation=cv2.INTER_CUBIC)  # after raise orbit, need resize from 0.55 to 0.5
    elif name.startswith("JL1KF02"):
        print("resize_by_sat JL1KF02")
        return cv2.resize(img, None, fx=0.98, fy=0.98, interpolation=cv2.INTER_CUBIC)
    elif name.startswith("JL1GF04A") or name.startswith("PAN.tif"):
        print("SR after RC.")
        return img
        # return cv2.resize(img, None, fx=5 / 6, fy=5 / 6, interpolation=cv2.INTER_CUBIC)
    else:
        print("resize_by_sat JL1KF01B JL1KF01C")
        return cv2.resize(img, None, fx=1.1, fy=1.1, interpolation=cv2.INTER_CUBIC)  # kf01b need resize from 0.55 to 0.5


def resize_by_sat_rcsc(img, name, prev_step):
    if prev_step == "RC":
        print("SR after RC without resize().")
        return img
    else:
        if name.startswith("JL1KF01A"):
            if get_img_date(name) < "20211205":  # before orbit, return 0.5
                print("resize_by_sat JL1KF01A 0.495 to 0.5")
                return cv2.resize(img, None, fx=0.99, fy=0.99, interpolation=cv2.INTER_CUBIC)
            else:
                # after raise orbit, need to resize from 0.55 to 0.5
                print("resize_by_sat JL1KF01A 0.55 to 0.5")
                return cv2.resize(img, None, fx=1.1, fy=1.1, interpolation=cv2.INTER_CUBIC)
        elif name.startswith("JL1KF02A"):
            if get_img_date(name) < "20231201":  # before orbit, return 0.5
                print("resize_by_sat JL1KF02A 0.49 to 0.5")
                return cv2.resize(img, None, fx=0.98, fy=0.98, interpolation=cv2.INTER_CUBIC)
            else:
                # after raise orbit, need to resize from 0.55 to 0.5
                print("resize_by_sat JL1KF02A 0.55 to 0.5")
                return cv2.resize(img, None, fx=1.1, fy=1.1, interpolation=cv2.INTER_CUBIC)
        elif name.startswith("JL1KF02B"):
            print("resize_by_sat JL1KF02B 0.49 to 0.5")
            return cv2.resize(img, None, fx=0.98, fy=0.98, interpolation=cv2.INTER_CUBIC)
        elif name.startswith("JL1GF05B"):
            print("resize_by_sat JL1GF05B 0.15 to 0.2")
            return cv2.resize(img, None, fx=0.75, fy=0.75, interpolation=cv2.INTER_CUBIC)
        else:
            # kf01b kf01c need to resize from 0.55 to 0.5
            print("resize_by_sat JL1KF01B JL1KF01C")
            return cv2.resize(img, None, fx=1.1, fy=1.1, interpolation=cv2.INTER_CUBIC)


def resize_by_sat_rcsc_2(img, name, resize_scale, prev_step):
    if prev_step == "RC":
        print("SR after RC without resize().")
        return img
    else:
        return cv2.resize(img, None, fx=float(resize_scale) / 2, fy=float(resize_scale) / 2, interpolation=cv2.INTER_CUBIC)
        # ---------- dead code below, kept for record ----------
        # if name.startswith("JL1KF01A"):
        if name.startswith("JL1KF01A"):
            if get_img_date(name) < "20211205":  # before orbit, return 0.5
                print("resize_by_sat JL1KF01A 0.495 to 0.5")
                return cv2.resize(img, None, fx=0.99, fy=0.99, interpolation=cv2.INTER_CUBIC)
            else:
                # after raise orbit, need to resize from 0.55 to 0.5
                print("resize_by_sat JL1KF01A 0.55 to 0.5")
                return cv2.resize(img, None, fx=1.1, fy=1.1, interpolation=cv2.INTER_CUBIC)
        elif name.startswith("JL1KF02A"):
            if get_img_date(name) < "20231201":  # before orbit, return 0.5
                print("resize_by_sat JL1KF02A 0.49 to 0.5")
                return cv2.resize(img, None, fx=0.98, fy=0.98, interpolation=cv2.INTER_CUBIC)
            else:
                # after raise orbit, need to resize from 0.55 to 0.5
                print("resize_by_sat JL1KF02A 0.55 to 0.5")
                return cv2.resize(img, None, fx=1.1, fy=1.1, interpolation=cv2.INTER_CUBIC)
        elif name.startswith("JL1KF02B"):
            print("resize_by_sat JL1KF02B 0.49 to 0.5")
            return cv2.resize(img, None, fx=0.98, fy=0.98, interpolation=cv2.INTER_CUBIC)
        elif name.startswith("JL1GF05B"):
            print("resize_by_sat JL1GF05B 0.15 to 0.2")
            return cv2.resize(img, None, fx=0.75, fy=0.75, interpolation=cv2.INTER_CUBIC)
        else:
            # kf01b kf01c need to resize from 0.55 to 0.5
            print("resize_by_sat JL1KF01B JL1KF01C")
            # return cv2.resize(img, None, fx=1.1, fy=1.1, interpolation=cv2.INTER_CUBIC)
def hist_match(image_ori, image_SR):
    (h_base, w_base) = image_ori.shape
    (h_tar, w_tar) = image_SR.shape
    Iout = np.copy(image_SR)
    num = 2 << 11
    LUT = np.zeros(num)
    hist_base = cv2.calcHist([image_ori], [0], None, [num], [0, num])
    hist_tar = cv2.calcHist([image_SR], [0], None, [num], [0, num])
    num_hist_base_0 = hist_base[0]
    num_hist_tar_0 = hist_tar[0]
    hist_base[0], hist_tar[0] = 0, 0
    nor_cdf_base = hist_base.cumsum() / (h_base * w_base - num_hist_base_0)
    nor_cdf_tar = hist_tar.cumsum() / (h_tar * w_tar - num_hist_tar_0)
    for i in range(1, num):
        min_var = 1.0
        for j in range(0, num):
            abs_minus_nor_cdf = abs(nor_cdf_tar[i] - nor_cdf_base[j])
            if abs_minus_nor_cdf < min_var:
                min_var = abs_minus_nor_cdf
                tag = j + 1
        LUT[i] = tag
    print(get_timestamp())
    for x in range(h_tar):
        for y in range(w_tar):
            Iout[x, y] = LUT[Iout[x, y]]
    return Iout


def write_print_gpu_mem_info(gpu_id, file_path):
    pynvml.nvmlInit()
    if gpu_id < 0 or gpu_id >= pynvml.nvmlDeviceGetCount():
        print('gpu_id {} not existed.'.format(gpu_id))
        return 0, 0, 0
    handler = pynvml.nvmlDeviceGetHandleByIndex(gpu_id)
    meminfo = pynvml.nvmlDeviceGetMemoryInfo(handler)
    total = round(meminfo.total / 1024 / 1024 / 1024, 2)
    used = round(meminfo.used / 1024 / 1024 / 1024, 2)
    free = round(meminfo.free / 1024 / 1024 / 1024, 2)
    if file_path is not None:
        with open(file_path, 'a') as srlog:
            srlog.writelines('\n    current gpu info: total {} GB, used {} GB, free {} GB, {}'.format(total, used, free, str(get_timestamp())))
    print('    current gpu info: total {} GB, used {} GB, free {} GB, {}'.format(total, used, free, str(get_timestamp())))
    # return total, used, free


def write_print_cpu_mem_info(file_path):
    total = round(psutil.virtual_memory().total / 1024 / 1024 / 1024, 2)
    free = round(psutil.virtual_memory().available / 1024 / 1024 / 1024, 2)
    used = round(psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024 / 1024, 2)
    if file_path is not None:
        with open(file_path, 'a') as srlog:
            srlog.writelines('\n    current cpu info: total {} GB, used {} GB, free {} GB, {}'.format(total, used, free, str(get_timestamp())))
    print('    current memory info: total {} GB, free {} GB, current process used {} GB'.format(total, free, used))
    # return mem_total, mem_free, mem_process_used


def write_print_nvsmi(gpu_id, file_path):
    proc = subprocess.Popen("nvidia-smi", stdout=subprocess.PIPE, shell=True)
    # proc.wait()
    cmd_out = proc.stdout.read()
    proc.stdout.close()
    if file_path is not None:
        with open(file_path, 'a') as srlog:
            srlog.writelines('\n    current gpu occupied info:\n' + cmd_out.decode(encoding='utf-8'))
    print(cmd_out.decode(encoding='utf-8'))


def get_direct_value(xml_config_file_path, tag):
    document_tree = parse(xml_config_file_path)  # tag to be replaced
    input_folder_path = document_tree.getElementsByTagName(tag)[0]
    value = input_folder_path.childNodes[0].data
    return value


def update_meta_gsd(path):
    prefixes = ("JL1GF04", "TEE01B")
    if osp.exists(path) and osp.basename(path).startswith(prefixes):
        rowGSD_value = np.float64(get_direct_value(path, "ImageRowGSD"))
        colGSD_value = np.float64(get_direct_value(path, "ImageColumnGSD"))
        update_tag(path, "ImageRowGSD", rowGSD_value / 2)
        update_tag(path, "ImageColumnGSD", colGSD_value / 2)
        print("Update ImageRowGSD and ImageColumnGSD for JL1GF04")
    else:
        return


def update_meta_integrationtime(path):
    actual_scl = 2
    img_name = osp.basename(path)
    if osp.exists(path):
        if img_name.startswith("JL1GF04"):
            actual_scl = 2
        elif img_name.startswith("JL1KF01B") or img_name.startswith("JL1KF01C"):
            actual_scl = 2.2
        elif img_name.startswith("JL1KF01A") and get_img_date(img_name) < "20211205":
            actual_scl = 2
        elif img_name.startswith("JL1KF01A") and get_img_date(img_name) >= "20211205":
            actual_scl = 2.2
        elif img_name.startswith("JL1KF02"):
            actual_scl = 1.96
        else:
            return
        integrationtime = np.float64(get_direct_value(path, "IntegrationTime"))
        update_tag(path, "IntegrationTime", np.round(integrationtime / actual_scl, 3))
        print("Update IntegrationTime {}/{}-->{}.".format(str(integrationtime), str(actual_scl), str(np.round(integrationtime / actual_scl, 3))))


def trt_test(x, model):
    torch.cuda.empty_cache()
    with torch.no_grad():
        y = model(x)
    torch.cuda.synchronize()
    return y


def tensor2img_fast_trt(tensor, out_type, min_max=(0, 1), min_dn=0, max_dn=1, max_DN=4095):
    output = tensor.squeeze()
    output = output.detach().clamp_(min_dn / max_DN, max_dn / max_DN)
    output = ((output - min_max[0]) / (min_max[1] - min_max[0])).cpu().numpy()
    output = (output * max_DN).round()
    return output.astype(out_type)


def tensor2img_fast_trt_3ch(tensor, out_type, min_max=(0, 1), min_dn=0, max_dn=1, max_DN=4095):
    output = tensor.squeeze()
    output = output.detach().clamp_(min_dn / max_DN, max_dn / max_DN)
    output = ((output - min_max[0]) / (min_max[1] - min_max[0])).cpu().numpy()[1, :, :]
    output = (output * max_DN).round()
    return output.astype(out_type)


def tensor2img_fast_trt_3(tensor, out_type, min_max=(0, 1), min_dn=0, max_dn=1, max_DN=4095):
    output = tensor.squeeze()
    output = output.detach().clamp_(min_dn / max_DN, max_dn / max_DN)
    output = ((output - min_max[0]) / (min_max[1] - min_max[0])).cpu().numpy()[1, :, :]
    output = (output * max_DN).round()
    return output.astype(out_type)


def check_gpuid(num):
    count = 0
    while "CUDA_VISIBLE_DEVICES" not in os.environ.keys():
        print(str(time.time()))
        time.sleep(2)
        print(str(time.time()))
        count += 1
        if count == num:
            print("Can not get CUDA_VISIBLE_DEVICES value.")
            exit(2)
    return True


def check_sr_previous_step(lq_path):
    document_tree = parse(osp.join(lq_path, osp.basename(lq_path) + "_meta.xml"))
    input_folder_path = document_tree.getElementsByTagName("SolarAzimuth")[0]
    value = input_folder_path.childNodes
    if value.length == 0:
        step = "RC"
        print('<SolarAzimuth></SolarAzimuth>')
    else:
        step = "SC"
        print('<SolarAzimuth>{:s}</SolarAzimuth>'.format(str(value[0].data)))
    return step
def filter2D(img, kernel):
    """PyTorch version of cv2.filter2D

    Args:
        img (Tensor): (b, c, h, w)
        kernel (Tensor): (b, k, k)
    """
    k = kernel.size(-1)
    # img = torch.unsqueeze(img, 0)
    b, c, h, w = img.size()
    if k % 2 == 1:
        img = F.pad(img, (k // 2, k // 2, k // 2, k // 2), mode='reflect')
    else:
        raise ValueError('Wrong kernel size')
    ph, pw = img.size()[-2:]
    if kernel.size(0) == 1:
        # apply the same kernel to all batch images
        img = img.view(b * c, 1, ph, pw)
        kernel = kernel.view(1, 1, k, k)
        return F.conv2d(img, kernel, padding=0).view(b, c, h, w)
    else:
        img = img.view(1, b * c, ph, pw)
        kernel = kernel.view(b, 1, k, k).repeat(1, c, 1, 1).view(b * c, 1, k, k)
        return F.conv2d(img, kernel, groups=b * c).view(b, c, h, w)


class USMSharp(torch.nn.Module):
    def __init__(self, radius=10, sigma=0):
        super(USMSharp, self).__init__()
        if radius % 2 == 0:
            radius += 1
        # self.radius = radius
        kernel = cv2.getGaussianKernel(radius, sigma)
        kernel = torch.FloatTensor(np.dot(kernel, kernel.transpose())).unsqueeze_(0)
        self.register_buffer('kernel', kernel)

    def forward(self, img, weight=1.0, threshold=10):
        blur = filter2D(img, self.kernel)
        residual = img - blur
        mask = torch.abs(residual) * 4095 > threshold
        mask = mask.float()
        # mask = torch.unsqueeze(mask, 0)
        soft_mask = filter2D(mask, self.kernel)
        sharp = img + weight * residual
        sharp = torch.clip(sharp, 0, 1)
        return soft_mask * sharp + (1 - soft_mask) * img
