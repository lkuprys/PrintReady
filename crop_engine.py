import os
import io
import sys
import gc
import numpy as np
from PIL import Image, ImageCms
import tifffile

def get_icc_profile_path() -> str:
    """Grąžina U.S. Web Coated (SWOP) v2 ICC profilio kelią."""
    if hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.abspath(".")
    icc_file = os.path.join(base_path, "us_web_coated_swop_v2.icc")
    return icc_file

def load_cmyk_profile():
    """Užkrauna U.S. Web Coated (SWOP) v2 ICC profilį."""
    icc_file = get_icc_profile_path()
    if os.path.exists(icc_file):
        with open(icc_file, "rb") as f:
            icc_bytes = f.read()
        return ImageCms.getOpenProfile(io.BytesIO(icc_bytes)), icc_bytes
    else:
        # Atsarginis variantas – sukurti standartinį CMYK profilį
        s_prof = ImageCms.createProfile('sRGB')
        return s_prof, None

def make_8bim_block(res_id: int, data: bytes) -> bytes:
    """Suformuoja standartinį Adobe Photoshop 8BIM Resource bloką su lyginiu baitų ilgiu."""
    name = b'\x00\x00'
    header = b'8BIM' + res_id.to_bytes(2, 'big') + name + len(data).to_bytes(4, 'big')
    if len(data) % 2 != 0:
        data += b'\x00'
    return header + data

def build_photoshop_exact_tags(spot_name: str = "W", solidity: int = 5, icc_bytes: bytes = None):
    """
    Sukuria 1:1 identiškus Adobe Photoshop 8BIM metaduomenis (Tag 34377),
    Tag 332 (InkSet), Tag 333 (InkNames) ir Tag 34675 (ICC Color Profile).
    """
    spot_name = spot_name.strip() or "W"

    # 1. Resource 1005: ResolutionInfo (300 DPI)
    res_1005_data = (
        (300).to_bytes(2, 'big') + (0).to_bytes(2, 'big') + (1).to_bytes(2, 'big') + (2).to_bytes(2, 'big') +
        (300).to_bytes(2, 'big') + (0).to_bytes(2, 'big') + (1).to_bytes(2, 'big') + (2).to_bytes(2, 'big')
    )
    res_1005 = make_8bim_block(1005, res_1005_data)

    # 2. Resource 1006 (0x03EE): Alpha Channel Names (Transparency + Spot W)
    p_trans = b'\x0cTransparency'
    p_spot = bytes([len(spot_name)]) + spot_name.encode('latin1')
    res_1006 = make_8bim_block(1006, p_trans + p_spot)

    # 3. Resource 1045 (0x0415): Unicode Alpha Names
    u_trans = (13).to_bytes(4, 'big') + 'Transparency'.encode('utf-16-be') + b'\x00\x00'
    u_spot = (len(spot_name) + 1).to_bytes(4, 'big') + spot_name.encode('utf-16-be') + b'\x00\x00'
    res_1045 = make_8bim_block(1045, u_trans + u_spot)

    # 4. Resource 1077 (0x0435): DisplayInfo v2 (Tiksli 30 baitų Photoshop struktūra su 4 baitų versijos antrašte)
    disp_v2_data = (
        (1).to_bytes(4, 'big') +                                                    # Versija = 1
        (0).to_bytes(2, 'big') +                                                    # Kan 1: ColorSpace = 0 (RGB)
        (65535).to_bytes(2, 'big') + (0).to_bytes(2, 'big') + (0).to_bytes(2, 'big') + (0).to_bytes(2, 'big') + # Red
        (100).to_bytes(2, 'big') +                                                  # Opacity = 100%
        (1).to_bytes(1, 'big') +                                                    # Kind = 1 (Mask/Transparency)
        (0).to_bytes(2, 'big') +                                                    # Kan 2: ColorSpace = 0 (RGB)
        (65535).to_bytes(2, 'big') + (0).to_bytes(2, 'big') + (0).to_bytes(2, 'big') + (0).to_bytes(2, 'big') + # Red
        int(solidity).to_bytes(2, 'big') +                                          # Solidity = 5%
        (2).to_bytes(1, 'big')                                                      # Kind = 2 (Spot Color)
    )
    res_1077 = make_8bim_block(1077, disp_v2_data)

    # 5. Resource 1050 (0x041A): Alpha Channel IDs
    id_data = (1).to_bytes(4, 'big') + (2).to_bytes(4, 'big')
    res_1050 = make_8bim_block(1050, id_data)

    bim_bytes = res_1005 + res_1006 + res_1045 + res_1077 + res_1050
    ink_names = f"Cyan\x00Magenta\x00Yellow\x00Black\x00Transparency\x00{spot_name}\x00"

    tags = [
        (332, 'H', 1, 1, True),                       # Tag 332: InkSet = 1 (CMYK + Spots)
        (333, 's', 0, ink_names, True),              # Tag 333: InkNames (ColorGATE ir Photoshop)
        (34377, 'B', len(bim_bytes), bim_bytes, True) # Tag 34377: Photoshop 8BIM
    ]

    if icc_bytes:
        tags.append((34675, 'B', len(icc_bytes), icc_bytes, True)) # Tag 34675: ICC Color Profile

    return tags

def process_and_crop(
    image_path: str,
    template_path: str,
    output_path: str,
    choke_pixels: int = 1,
    spot_channel_name: str = "W",
    solidity: int = 5,
    target_dpi: int = 300
) -> bool:
    """
    1. Nuskaito kliento nuotrauką ir .PNG šabloną.
    2. Proporcingai išdidina ir sucentruoja (Aspect Cover / Fill).
    3. Tiksliai konvertuoja RGB -> CMYK naudojant profesionalų U.S. Web Coated (SWOP) v2 ICC profilį.
    4. Apkerpa pagal šablono formą (išorė lieka 100% permatoma).
    5. Suformuoja 6-ių kanalų CMYK + Transparency + Spot White W masyvą (uint8, 0..255).
    6. Išsaugo paruoštą 300 DPI spaudos .TIF failą su įterptu ICC profiliu ir 8BIM metaduomenimis.
    """
    # 1. Nuskaitome šabloną
    with Image.open(template_path) as t_img:
        template_rgba = t_img.convert("RGBA")
        t_w, t_h = template_rgba.size
        t_arr = np.array(template_rgba)

    t_alpha = t_arr[..., 3]
    if np.count_nonzero(t_alpha) == 0:
        raise ValueError(f"Šablonas {os.path.basename(template_path)} yra visiškai permatomas/tuščias!")

    template_mask = t_alpha > 0

    # 2. Nuskaitome kliento nuotrauką
    with Image.open(image_path) as c_img:
        try:
            from PIL import ImageOps
            c_img = ImageOps.exif_transpose(c_img)
        except Exception:
            pass

        # Patikriname ar originali nuotrauka turi įterptą ICC profilį
        src_icc = c_img.info.get('icc_profile')
        if src_icc:
            try:
                src_profile = ImageCms.getOpenProfile(io.BytesIO(src_icc))
            except Exception:
                src_profile = ImageCms.createProfile('sRGB')
        else:
            src_profile = ImageCms.createProfile('sRGB')

        c_rgba = c_img.convert("RGBA")
        img_w, img_h = c_rgba.size

        # Proporcingas mastelis (Aspect Cover / Fill)
        scale = max(t_w / img_w, t_h / img_h)
        new_w = int(round(img_w * scale))
        new_h = int(round(img_h * scale))

        # Išdidiname su aukštos kokybės LANCZOS filtru
        resized_img = c_rgba.resize((new_w, new_h), Image.Resampling.LANCZOS)

        # Centruojame
        left = (new_w - t_w) // 2
        top = (new_h - t_h) // 2
        cropped_rgba = resized_img.crop((left, top, left + t_w, top + t_h))

    del c_rgba, resized_img
    gc.collect()

    # 3. Tiksli spalvų konversija: RGB -> CMYK per U.S. Web Coated (SWOP) v2 ICC profilį
    cmyk_profile, icc_bytes = load_cmyk_profile()
    rgb_for_cmyk = cropped_rgba.convert("RGB")
    try:
        cmyk_img = ImageCms.profileToProfile(
            rgb_for_cmyk,
            src_profile,
            cmyk_profile,
            outputMode='CMYK',
            renderingIntent=ImageCms.Intent.PERCEPTUAL
        )
        cmyk_arr = np.array(cmyk_img)
    except Exception:
        # Atsarginis būdas, jei ImageCms nepavyktų
        cmyk_img = rgb_for_cmyk.convert("CMYK")
        cmyk_arr = np.array(cmyk_img)

    del rgb_for_cmyk, cmyk_img
    gc.collect()

    # 4. Kaukės ir permatomumas
    img_arr = np.array(cropped_rgba)
    img_alpha = img_arr[..., 3]
    final_mask = template_mask & (img_alpha > 0)

    h, w = t_h, t_w
    out_arr = np.zeros((h, w, 6), dtype=np.uint8)

    # Įrašome tikrąsias CMYK spalvas į 0..3 kanalus
    out_arr[..., 0:4] = cmyk_arr

    # Išvalome fono pikselius už šablono ribų į 0% CMYK
    bg_mask = ~final_mask
    out_arr[..., 0][bg_mask] = 0
    out_arr[..., 1][bg_mask] = 0
    out_arr[..., 2][bg_mask] = 0
    out_arr[..., 3][bg_mask] = 0

    # 4-asis kanalas: Sluoksnio skaidrumo Alpha kanalas (Layer Transparency)
    out_arr[..., 4][final_mask] = 255

    # 5. Baltas Spot kanalas W su 1px Choke sutraukimu
    mask_to_choke = final_mask.copy()
    if choke_pixels > 0:
        p = choke_pixels
        eroded = np.zeros_like(mask_to_choke)
        eroded[p:-p, p:-p] = (
            mask_to_choke[p:-p, p:-p] & 
            mask_to_choke[:-p*2, p:-p] & 
            mask_to_choke[p*2:, p:-p] & 
            mask_to_choke[p:-p, :-p*2] & 
            mask_to_choke[p:-p, p*2:]
        )
        mask_to_choke = eroded
        del eroded

    # 5-asis kanalas: Spot White W (0 po dizainu = 100% baltas rašalas, 255 fone = 0% baltas rašalas)
    white_channel = np.full((h, w), fill_value=255, dtype=np.uint8)
    white_channel[mask_to_choke] = 0
    out_arr[..., 5] = white_channel

    del mask_to_choke, white_channel, bg_mask, final_mask, template_mask, img_arr, t_arr, cmyk_arr
    gc.collect()

    # 6. Išsaugome TIFF failą pagal 1:1 Photoshop ir ColorGATE standartą
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    spot_tags = build_photoshop_exact_tags(spot_channel_name, solidity=solidity, icc_bytes=icc_bytes)

    tifffile.imwrite(
        output_path,
        out_arr,
        photometric='separated',
        extrasamples=[1, 0], # 1 = ASSOCALPHA (Layer Transparency), 0 = UNSPECIFIED (Spot W)
        byteorder='>',        # Macintosh Byte Order
        compression='lzw',    # LZW Compression
        resolution=(target_dpi, target_dpi, 'inch'),
        extratags=spot_tags
    )

    del out_arr
    gc.collect()
    return True
