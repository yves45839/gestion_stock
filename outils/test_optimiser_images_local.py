from contextlib import redirect_stdout
from io import BytesIO, StringIO
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
import optimiser_images_local as tool

SITE='https://samr.pythonanywhere.com'


class ImageTests(unittest.TestCase):
    def test_already_small_optimised_png_is_not_replaced_without_gain(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'original.png';target=root/'new.png'
            Image.new('RGB',(1,1),'white').save(source,optimize=True,compress_level=9)
            before=source.read_bytes()
            self.assertEqual(tool.optimise(source,target)['status'],'pas de gain — conserver original')
            self.assertFalse(target.exists());self.assertEqual(source.read_bytes(),before)

    def test_existing_candidate_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'original.png';target=root/'new.png'
            Image.new('RGB',(2400,1200),'blue').save(source)
            target.write_bytes(b'keep existing data')
            with self.assertRaises(ValueError):tool.optimise(source,target)
            self.assertEqual(target.read_bytes(),b'keep existing data')

    def test_png_resize_preserves_alpha_format_and_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'original.png';target=root/'new.png'
            Image.new('RGBA',(2400,1200),(30,60,90,64)).save(source)
            original=source.read_bytes()
            result=tool.optimise(source,target,1600)
            self.assertEqual(result['status'],'optimisée et vérifiée')
            self.assertEqual(source.read_bytes(),original)
            with Image.open(target) as image:
                self.assertEqual(image.format,'PNG');self.assertEqual(image.size,(1600,800))
                self.assertEqual(image.getpixel((0,0))[3],64)

    def test_jpeg_orientation_is_applied_without_cropping(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'original.jpg';target=root/'new.jpg'
            image=Image.effect_noise((2000,1000),80).convert('RGB')
            exif=Image.Exif();exif[274]=6
            image.save(source,quality=100,exif=exif)
            before=source.read_bytes()
            result=tool.optimise(source,target,1600)
            self.assertEqual(result['source_dimensions'],[1000,2000])
            with Image.open(target) as output:
                self.assertEqual(output.size,(800,1600))
                self.assertEqual(output.format,'JPEG')
                self.assertNotEqual(output.getexif().get(274),6)
            self.assertEqual(source.read_bytes(),before)

    def test_no_upscaling(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'original.jpg';target=root/'new.jpg'
            Image.effect_noise((80,40),80).convert('RGB').save(source,quality=100)
            result=tool.optimise(source,target,1600)
            self.assertEqual(result['output_dimensions'],[80,40])

    def test_animated_images_are_preserved_without_flattening(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'animated.gif';target=root/'new.gif'
            images=[Image.new('RGB',(50,50),color) for color in ['red','blue']]
            images[0].save(source,save_all=True,append_images=images[1:],duration=100,loop=0)
            self.assertIn('animée',tool.optimise(source,target)['status'])
            self.assertFalse(target.exists())

    def test_failed_save_keeps_original_and_cleans_partial_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'original.png';target=root/'new.png'
            Image.new('RGB',(100,100),'red').save(source)
            before=source.read_bytes()
            with patch.object(Image.Image,'save',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):tool.optimise(source,target)
            self.assertEqual(source.read_bytes(),before)
            self.assertFalse(target.exists());self.assertEqual(list(root.glob('travail-*')),[])

    def test_catalog_contains_images_and_pending_images_once(self):
        class Response(StringIO):url=SITE+'/api/products/'
        data={'count':2,'results':[
            {'image_url':SITE+tool.PREFIX+'a.png','pending_image_url':SITE+tool.PREFIX+'pending/b.jpg'},
            {'image_url':SITE+tool.PREFIX+'a.png'}]}
        with patch.object(tool,'open_url',return_value=Response(json.dumps(data))):
            result=tool.fetch_catalog(SITE)
        self.assertEqual(set(result),{'products/images/a.png','products/images/pending/b.jpg'})

    def test_full_run_and_resume_preserve_backup_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);fixture=root/'source.png';output=root/'backup'
            Image.new('RGBA',(2400,1200),(30,60,90,64)).save(fixture)
            key='products/images/a.png'
            original_hash=tool.sha256(fixture)
            def copy(url,target):
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(fixture,target)
            with patch.object(tool.sys,'argv',['tool','--output',str(output)]),patch.object(tool,'fetch_catalog',return_value={key:SITE+tool.PREFIX+'a.png'}),patch.object(tool,'download',side_effect=copy) as download,redirect_stdout(StringIO()):
                self.assertEqual(tool.main(),0);self.assertEqual(tool.main(),0)
                self.assertEqual(download.call_count,1)
            report=json.loads((output/'rapport-images.json').read_text())
            self.assertEqual(report['files'][key]['original_sha256'],original_hash)
            self.assertEqual(tool.sha256(output/'originaux'/key),original_hash)
            self.assertLess(report['files'][key]['optimized_bytes'],report['files'][key]['original_bytes'])

    def test_unsafe_urls_are_rejected(self):
        for url in ['https://other.example/media/products/images/a.png',
                    '/media/products/images/../a.png','/media/products/images/CON.png']:
            with self.subTest(url=url),self.assertRaises(ValueError):tool.media_path(url,SITE)

    def test_html_is_not_saved_as_an_image(self):
        response=BytesIO(b'<html>login</html>');response.url=SITE+tool.PREFIX+'a.png'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'original.png'
            with patch.object(tool,'open_url',return_value=response):
                with self.assertRaises(ValueError):tool.download(response.url,target)
            self.assertEqual(list(root.iterdir()),[])


if __name__=='__main__':unittest.main()
