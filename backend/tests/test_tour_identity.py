"""Synthetic headers only; no SR, GPU or production data."""
from unittest.mock import patch

from PIL import Image, TiffImagePlugin
import pytest

from backend.services.tour_identity import image_identity
from backend.tests.test_scene_siblings import SiblingsBase


class TestTourSiblings(SiblingsBase):
    def test_unregistered_production_names_and_old_are_returned_for_diagnostics(self):
        directory = self.make_scene()
        Image.new('L', (80, 40)).save(directory / (directory.name + '_NOSR.tif'))
        Image.new('L', (160, 80)).save(directory / (directory.name + '_old.tif'))
        client = self.client()
        row = self.resolve(client, directory)
        with patch('os.listdir', side_effect=AssertionError('scan')), patch('os.scandir', side_effect=AssertionError('scan')):
            body = self.q(client, row['id'], tour='true', known=[row['id']])
        images = body['tourItems']
        assert {i['tourIdentity']['role'] for i in images} == {'pan', 'nosr', 'old'}
        assert all('registration' not in i['tourIdentity'] for i in images)
        assert all(i['tourIdentity']['evidenceReason'] for i in images)

    def test_known_ids_are_bounded_and_do_not_authorize_unknown_products(self):
        directory = self.make_scene()
        client = self.client()
        row = self.resolve(client, directory)
        rejected = self.siblings(client, row['id'], tour='true', known=['bad'] * 33)
        assert rejected.status_code == 400
        result = self.q(client, row['id'], tour='true', known=['bad'])
        assert result['tourDiagnostics']

    def test_opt_in_scene_evidence_is_bounded_and_explicit(self):
        directory = self.make_scene()
        (directory / (directory.name + '_meta.xml')).write_text('<SolarAzimuth>1</SolarAzimuth>')
        write_geo(directory / (directory.name + '.tif'))
        write_geo(directory / (directory.name + '_NOSR.tif'), 40, 20, 4)
        write_geo(directory / (directory.name + '_ori.tif'))
        client = self.client()
        row = self.resolve(client, directory)
        with patch('os.listdir', side_effect=AssertionError('scan')), patch('os.scandir', side_effect=AssertionError('scan')):
            body = self.q(client, row['id'], tour='true')
        images = body['tourItems']
        assert {i['tourIdentity']['role'] for i in images} == {'pan', 'nosr', 'ori'}
        assert all(i['tourIdentity']['sourceRevision'] == i['srcRev'] for i in images)
        assert all(i['tourIdentity']['registration'] == images[0]['tourIdentity']['registration'] for i in images)


def write_geo(path, width=80, height=40, scale=2, left=100, crs=32650, raster=1):
    tags = TiffImagePlugin.ImageFileDirectory_v2()
    tags[33550] = (float(scale), float(scale), 0.)
    tags.tagtype[33550] = 12
    tags[33922] = (0., 0., 0., float(left), 200., 0.)
    tags.tagtype[33922] = 12
    tags[34735] = (1, 1, 0, 3, 1024, 0, 1, 1, 1025, 0, 1, raster, 3072, 0, 1, crs)
    Image.new('L', (width, height), 123).save(path, tiffinfo=tags)


def test_explicit_registered_grids_and_no_directory_scan(tmp_path):
    a, b = tmp_path/'PAN.tif', tmp_path/'PAN_NOSR.tif'
    write_geo(a)
    write_geo(b, 40, 20, 4)
    with patch('os.listdir', side_effect=AssertionError('scan')), patch('os.scandir', side_effect=AssertionError('scan')):
        x, y = image_identity(a, 'production-sequence', 'pan'), image_identity(b, 'production-sequence', 'nosr')
    assert x['registration'] == y['registration']
    assert x['sourceRevision'] and y['sourceRevision']
    assert x['width'] == 80 and y['width'] == 40


@pytest.mark.parametrize('raster,crs', [(2, 32650), (1, 32767)])
def test_insufficient_evidence_refused(tmp_path, raster, crs):
    p = tmp_path/'PAN_ori.tif'
    write_geo(p, raster=raster, crs=crs)
    assert 'registration' not in image_identity(p, 's', 'ori')


def test_missing_geo_and_changed_source(tmp_path):
    p = tmp_path/'PAN.tif'
    Image.new('L', (8, 4)).save(p)
    assert 'registration' not in image_identity(p, 's', 'pan')
    write_geo(p)
    with patch('backend.services.tour_identity.source_revision', side_effect=['before', 'after']):
        result = image_identity(p, 's', 'pan')
    assert 'registration' not in result and not result['sourceRevision']


def test_shift_is_preserved_as_evidence(tmp_path):
    a, b = tmp_path/'PAN.tif', tmp_path/'PAN_ori.tif'
    write_geo(a)
    write_geo(b, left=101)
    assert image_identity(a, 's', 'pan')['registration'] != image_identity(b, 's', 'ori')['registration']
