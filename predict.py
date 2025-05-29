import os
import torch
from PIL import Image
from cog import BasePredictor, Path, Input

from flux.sampling import denoise, get_schedule, prepare_kontext, unpack
from flux.util import (
    configs,
    load_clip,
    load_t5
)
from flux.model import Flux
from flux.modules.autoencoder import AutoEncoder
from safetensors.torch import load_file as load_sft
from safety_checker import SafetyChecker
from util import print_timing, warm_up_model
from weights import download_weights

from torchao.quantization import quantize_, Float8DynamicActivationFloat8WeightConfig
from torchao.quantization.granularity import PerTensor, PerRow

torch._dynamo.config.recompile_limit = 40

FP8_QUANTIZATION = True
# Kontext model configuration
KONTEXT_WEIGHTS_URL = "https://weights.replicate.delivery/default/black-forest-labs/kontext/pre-release/preliminary-dev-kontext.sft"
KONTEXT_WEIGHTS_PATH = "/models/kontext/preliminary-dev-kontext.sft"

# Model weights URLs
AE_WEIGHTS_URL = "https://weights.replicate.delivery/default/black-forest-labs/FLUX.1-dev/safetensors/ae.safetensors"
AE_WEIGHTS_PATH = "/models/flux-dev/ae.safetensors"

TORCH_COMPILE_CACHE_UNQUANTIZED = "./torch-compile-cache-flux-dev-kontext.bin"
TORCH_COMPILE_CACHE_FP8 = "./torch-compile-cache-flux-dev-kontext-fp8.bin"

if FP8_QUANTIZATION:
    TORCH_COMPILE_CACHE = TORCH_COMPILE_CACHE_FP8
else:
    TORCH_COMPILE_CACHE = TORCH_COMPILE_CACHE_UNQUANTIZED

# all of these aspect ratio should also be in flux.util.PREFERED_KONTEXT_RESOLUTIONS
# these are width, height pairs
ASPECT_RATIOS = {
    "1:1": (1024, 1024),
    "16:9": (1328, 800),
    "21:9": (1568, 672),
    "3:2": (1248, 832),
    "2:3": (832, 1248),
    "4:5": (944, 1104),
    "5:4": (1104, 944),
    "3:4": (880, 1184),
    "4:3": (1184, 880),
    "9:16": (800, 1328),
    "9:21": (672, 1568),
    "match_input_image": (None, None),
}

def quantize_filter_fn(m, name):
    if isinstance(m, torch.nn.Linear) and "single_blocks" in name and ("linear1" in name or "linear2" in name):
        return True
    else:
        return False


class FluxDevKontextPredictor(BasePredictor):
    """
    Flux.1 Kontext Predictor - Image-to-image transformation model using FLUX.1-dev architecture
    """

    def setup(self) -> None:
        """Load model weights and initialize the pipeline"""
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        # Download all weights if needed
        download_model_weights()

        # Initialize models
        self.t5 = load_t5(self.device, max_length=512)
        self.clip = load_clip(self.device)
        self.model = load_kontext_model(device=self.device)
        self.ae = load_ae_local(device=self.device)

        # load the torch compile cache
        if os.path.exists(TORCH_COMPILE_CACHE):
            with open(TORCH_COMPILE_CACHE, "rb") as f:
                artifact_bytes = f.read()
                torch.compiler.load_cache_artifacts(artifact_bytes)
        else:
            print(f"WARNING:Torch compile cache not found at {TORCH_COMPILE_CACHE}")

        
        if FP8_QUANTIZATION:
            quantize_(self.model, Float8DynamicActivationFloat8WeightConfig(granularity=PerTensor()), filter_fn=quantize_filter_fn)
        self.model = torch.compile(self.model, dynamic=False)

        for (h,w) in ASPECT_RATIOS.values():
            if (h,w) == (None, None):
                continue
            with print_timing(f"warm up model for aspect ratio {h}x{w}"):
                warm_up_model(h, w, self.model)

        # Initialize safety checker
        self.safety_checker = SafetyChecker()
        print("FluxDevKontextPredictor setup complete")

    # def size_from_aspect_megapixels(
    #     self, aspect_ratio: str, megapixels: str = "1"
    # ) -> tuple[int | None, int | None]:
    #     """Convert aspect ratio and megapixels to width and height"""
    #     width, height = ASPECT_RATIOS[aspect_ratio]
    #     if width is None or height is None:
    #         # For match_input_image, return None values
    #         return (None, None)
    #     if megapixels == "0.25":
    #         width, height = width // 2, height // 2
    #     return (width, height)

    def predict(
        self,
        prompt: str = Input(
            description="Text description of what you want to generate, or the instruction on how to edit the given image.",
        ),
        input_image: Path = Input(
            description="Image to use as reference. Must be jpeg, png, gif, or webp.",
        ),
        aspect_ratio: str = Input(
            description="Aspect ratio of the generated image. Use 'match_input_image' to match the aspect ratio of the input image.",
            choices=list(ASPECT_RATIOS.keys()),
            default="match_input_image",
        ),
        # megapixels: str = Input(
        #     description="Approximate number of megapixels for generated image",
        #     choices=["1", "0.25"],
        #     default="1",
        # ),
        num_inference_steps: int = Input(
            description="Number of inference steps", default=30, ge=4, le=50
        ),
        guidance: float = Input(
            description="Guidance scale for generation", default=2.5, ge=0.0, le=10.0
        ),
        seed: int = Input(
            description="Random seed for reproducible generation. Leave blank for random.",
            default=None,
        ),
        output_format: str = Input(
            description="Output image format",
            choices=["webp", "jpg", "png"],
            default="webp",
        ),
        output_quality: int = Input(
            description="Quality when saving the output images, from 0 to 100. 100 is best quality, 0 is lowest quality. Not relevant for .png outputs",
            default=80,
            ge=0,
            le=100,
        ),
        disable_safety_checker: bool = Input(
            description="Disable NSFW safety checker", default=False
        ),
    ) -> Path:
        """
        Generate an image based on the text prompt and conditioning image using FLUX.1 Kontext
        """
        with torch.inference_mode(), print_timing("generate image"):
            seed = prepare_seed(seed)

            if aspect_ratio == "match_input_image":
                target_width, target_height = None, None
            else:
                target_width, target_height = ASPECT_RATIOS[aspect_ratio]

            # Prepare input for kontext sampling
            with print_timing("prepare input"):
                inp, final_height, final_width = prepare_kontext(
                    t5=self.t5,
                    clip=self.clip,
                    prompt=prompt,
                    ae=self.ae,
                    img_cond_path=str(input_image),
                    target_width=target_width,
                    target_height=target_height,
                    bs=1,
                    seed=seed,
                    device=self.device,
                )

            # Remove the original conditioning image from memory to save space
            inp.pop("img_cond_orig", None)

            # Get sampling schedule
            timesteps = get_schedule(
                num_inference_steps,
                inp["img"].shape[1],
                shift=True,  # flux-dev uses shift=True
            )

            # Generate image
            with print_timing("denoise"):
                x = denoise(self.model, **inp, timesteps=timesteps, guidance=guidance)

            # Decode latents to pixel space
            with print_timing("decode"):
                x = unpack(x.float(), final_height, final_width)
                with torch.autocast(device_type=self.device.type, dtype=torch.bfloat16):
                    x = self.ae.decode(x)

            with print_timing("convert to image"):
                x = x.clamp(-1, 1)
                x = (x + 1) / 2
                x = (x.permute(0, 2, 3, 1) * 255).to(torch.uint8).cpu().numpy()
                image = Image.fromarray(x[0])

            # Apply safety checking
            if not disable_safety_checker:
                with print_timing("Running safety checker"):
                    images = self.safety_checker.filter_images([image])
                    if not images:
                        raise Exception(
                            "Generated image contained NSFW content. Try running it again with a different prompt."
                        )
                    image = images[0]

            # Save image
            output_path = f"output.{output_format}"
            if output_format == "png":
                image.save(output_path)
            elif output_format == "webp":
                image.save(
                    output_path, format="WEBP", quality=output_quality, optimize=True
                )
            else:  # jpg
                image.save(
                    output_path, format="JPEG", quality=output_quality, optimize=True
                )

            # Return the output path
            return Path(output_path)


def download_model_weights():
    """Download all required model weights if they don't exist"""
    # Download kontext weights
    if not os.path.exists(KONTEXT_WEIGHTS_PATH):
        print("Kontext weights not found, downloading...")
        download_weights(KONTEXT_WEIGHTS_URL, Path(KONTEXT_WEIGHTS_PATH))
        print("Kontext weights downloaded successfully")
    else:
        print("Kontext weights already exist")

    # Download autoencoder weights
    if not os.path.exists(AE_WEIGHTS_PATH):
        print("Autoencoder weights not found, downloading...")
        download_weights(AE_WEIGHTS_URL, Path(AE_WEIGHTS_PATH))
        print("Autoencoder weights downloaded successfully")
    else:
        print("Autoencoder weights already exist")


def load_kontext_model(device: str | torch.device = "cuda"):
    """Load the kontext model with complete transformer weights"""
    # Use flux-dev config as base for kontext model
    config = configs["flux-dev"]

    print("Loading kontext model...")
    with torch.device("meta"):
        model = Flux(config.params).to(torch.bfloat16)

    # Load kontext weights (complete transformer)
    print(f"Loading kontext weights from {KONTEXT_WEIGHTS_PATH}")
    sd = load_sft(KONTEXT_WEIGHTS_PATH, device=str(device))
    missing, unexpected = model.load_state_dict(sd, strict=False, assign=True)

    if missing:
        print(f"Missing keys: {missing}")
    if unexpected:
        print(f"Unexpected keys: {unexpected}")

    return model


def load_ae_local(device: str | torch.device = "cuda"):
    """Load autoencoder from local weights"""
    config = configs["flux-dev"]

    print("Loading autoencoder...")
    with torch.device("meta"):
        ae = AutoEncoder(config.ae_params)

    print(f"Loading autoencoder weights from {AE_WEIGHTS_PATH}")
    sd = load_sft(AE_WEIGHTS_PATH, device=str(device))
    missing, unexpected = ae.load_state_dict(sd, strict=False, assign=True)

    if missing:
        print(f"AE Missing keys: {missing}")
    if unexpected:
        print(f"AE Unexpected keys: {unexpected}")

    return ae


def prepare_seed(seed: int) -> int:
    if not seed:
        seed = int.from_bytes(os.urandom(2), "big")
    print(f"Using seed: {seed}")
    return seed
