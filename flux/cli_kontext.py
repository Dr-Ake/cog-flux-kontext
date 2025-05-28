import os
import re
import time
from dataclasses import dataclass
from glob import iglob

import torch
from fire import Fire
from transformers import pipeline

from flux.sampling import denoise, get_schedule, prepare_kontext, unpack
from flux.util import (
    aspect_ratio_to_height_width,
    configs,
    load_ae,
    load_clip,
    load_flow_model,
    load_t5,
    save_image,
)


@dataclass
class SamplingOptions:
    prompt: str
    width: int | None
    height: int | None
    num_steps: int
    guidance: float
    seed: int | None
    img_cond_path: str


def parse_prompt(options: SamplingOptions) -> SamplingOptions | None:
    user_question = "Next prompt (write /h for help, /q to quit and leave empty to repeat):\n"
    usage = (
        "Usage: Either write your prompt directly, leave this field empty "
        "to repeat the prompt or write a command starting with a slash:\n"
        "- '/ar <width>:<height>' will set the aspect ratio of the generated image\n"
        "- '/s <seed>' sets the next seed\n"
        "- '/g <guidance>' sets the guidance (flux-dev only)\n"
        "- '/n <steps>' sets the number of steps\n"
        "- '/q' to quit"
    )

    while (prompt := input(user_question)).startswith("/"):
        if prompt.startswith("/ar"):
            if prompt.count(" ") != 1:
                print(f"Got invalid command '{prompt}'\n{usage}")
                continue
            _, ratio_prompt = prompt.split()
            if ratio_prompt == "auto":
                options.width = None
                options.height = None
                print("Setting resolution to input image resolution.")
            else:
                options.width, options.height = aspect_ratio_to_height_width(ratio_prompt)
                print(f"Setting resolution to {options.width} x {options.height}.")
        elif prompt.startswith("/h"):
            if prompt.count(" ") != 1:
                print(f"Got invalid command '{prompt}'\n{usage}")
                continue
            _, height = prompt.split()
            if height == "auto":
                options.height = None
            else:
                options.height = 16 * (int(height) // 16)
            if options.height is not None and options.width is not None:
                print(
                    f"Setting resolution to {options.width} x {options.height} "
                    f"({options.height *options.width/1e6:.2f}MP)"
                )
            else:
                print(f"Setting resolution to {options.width} x {options.height}.")
        elif prompt.startswith("/g"):
            if prompt.count(" ") != 1:
                print(f"Got invalid command '{prompt}'\n{usage}")
                continue
            _, guidance = prompt.split()
            options.guidance = float(guidance)
            print(f"Setting guidance to {options.guidance}")
        elif prompt.startswith("/s"):
            if prompt.count(" ") != 1:
                print(f"Got invalid command '{prompt}'\n{usage}")
                continue
            _, seed = prompt.split()
            options.seed = int(seed)
            print(f"Setting seed to {options.seed}")
        elif prompt.startswith("/n"):
            if prompt.count(" ") != 1:
                print(f"Got invalid command '{prompt}'\n{usage}")
                continue
            _, steps = prompt.split()
            options.num_steps = int(steps)
            print(f"Setting number of steps to {options.num_steps}")
        elif prompt.startswith("/q"):
            print("Quitting")
            return None
        else:
            if not prompt.startswith("/h"):
                print(f"Got invalid command '{prompt}'\n{usage}")
            print(usage)
    if prompt != "":
        options.prompt = prompt
    return options


def parse_img_cond_path(options: SamplingOptions | None) -> SamplingOptions | None:
    if options is None:
        return None

    user_question = "Next input image (write /h for help, /q to quit and leave empty to repeat):\n"
    usage = (
        "Usage: Either write a path to an image directly, leave this field empty "
        "to repeat the last input image or write a command starting with a slash:\n"
        "- '/q' to quit\n\n"
        "The input image will be edited by FLUX.1 Kontext creating a new image based"
        "on your instruction prompt."
    )

    while True:
        img_cond_path = input(user_question)

        if img_cond_path.startswith("/"):
            if img_cond_path.startswith("/q"):
                print("Quitting")
                return None
            else:
                if not img_cond_path.startswith("/h"):
                    print(f"Got invalid command '{img_cond_path}'\n{usage}")
                print(usage)
            continue

        if img_cond_path == "":
            break

        if not os.path.isfile(img_cond_path) or not img_cond_path.lower().endswith(
            (".jpg", ".jpeg", ".png", ".webp")
        ):
            print(f"File '{img_cond_path}' does not exist or is not a valid image file")
            continue

        options.img_cond_path = img_cond_path
        break

    return options


@torch.inference_mode()
def main(
    name: str = "flux-dev",
    aspect_ratio: str | None = None,
    seed: int | None = None,
    prompt: str = "replace the logo with the text 'Hello World'",
    device: str = "cuda" if torch.cuda.is_available() else "cpu",
    num_steps: int = 30,
    loop: bool = False,
    guidance: float = 2.5,
    offload: bool = False,
    output_dir: str = "output",
    add_sampling_metadata: bool = True,
    img_cond_path: str = "assets/cup.png",
    **kwargs: dict | None,
):
    """
    Sample the flux model. Either interactively (set `--loop`) or run for a
    single image.

    Args:
        height: height of the sample in pixels (should be a multiple of 16), None
            defaults to the size of the conditioning
        width: width of the sample in pixels (should be a multiple of 16), None
            defaults to the size of the conditioning
        seed: Set a seed for sampling
        output_name: where to save the output image, `{idx}` will be replaced
            by the index of the sample
        prompt: Prompt used for sampling
        device: Pytorch device
        num_steps: number of sampling steps (default 4 for schnell, 50 for guidance distilled)
        loop: start an interactive session and sample multiple times
        guidance: guidance value used for guidance distillation
        add_sampling_metadata: Add the prompt to the image Exif metadata
        img_cond_path: path to conditioning image (jpeg/png/webp)
        trt: use TensorRT backend for optimized inference
    """
    assert name == "flux-dev", f"Got unknown model name: {name}"

    nsfw_classifier = pipeline("image-classification", model="Falconsai/nsfw_image_detection", device=device)

    if name not in configs:
        available = ", ".join(configs.keys())
        raise ValueError(f"Got unknown model name: {name}, chose from {available}")

    torch_device = torch.device(device)

    output_name = os.path.join(output_dir, "img_{idx}.jpg")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        idx = 0
    else:
        fns = [fn for fn in iglob(output_name.format(idx="*")) if re.search(r"img_[0-9]+\.jpg$", fn)]
        if len(fns) > 0:
            idx = max(int(fn.split("_")[-1].split(".")[0]) for fn in fns) + 1
        else:
            idx = 0

    if aspect_ratio is None:
        width = None
        height = None
    else:
        width, height = aspect_ratio_to_height_width(aspect_ratio)

    # init all components
    t5 = load_t5(torch_device, max_length=512)
    clip = load_clip(torch_device)
    model = load_flow_model(name, device="cpu" if offload else torch_device)
    ae = load_ae(name, device="cpu" if offload else torch_device)

    rng = torch.Generator(device="cpu")
    opts = SamplingOptions(
        prompt=prompt,
        width=width,
        height=height,
        num_steps=num_steps,
        guidance=guidance,
        seed=seed,
        img_cond_path=img_cond_path,
    )

    if loop:
        opts = parse_prompt(opts)
        opts = parse_img_cond_path(opts)

    while opts is not None:
        if opts.seed is None:
            opts.seed = rng.seed()
        print(f"Generating with seed {opts.seed}:\n{opts.prompt}")
        t0 = time.perf_counter()

        if offload:
            t5, clip, ae = t5.to(torch_device), clip.to(torch_device), ae.to(torch_device)
        inp, height, width = prepare_kontext(
            t5=t5,
            clip=clip,
            prompt=opts.prompt,
            ae=ae,
            img_cond_path=opts.img_cond_path,
            target_width=opts.width,
            target_height=opts.height,
            bs=1,
            seed=opts.seed,
            device=torch_device,
        )
        from safetensors.torch import save_file

        save_file({k: v.cpu().contiguous() for k, v in inp.items()}, "output/noise.sft")
        inp.pop("img_cond_orig")
        opts.seed = None
        timesteps = get_schedule(opts.num_steps, inp["img"].shape[1], shift=(name != "flux-schnell"))

        # offload TEs and AE to CPU, load model to gpu
        if offload:
            t5, clip, ae = t5.cpu(), clip.cpu(), ae.cpu()
            torch.cuda.empty_cache()
            model = model.to(torch_device)

        # denoise initial noise
        x = denoise(model, **inp, timesteps=timesteps, guidance=opts.guidance)

        # offload model, load autoencoder to gpu
        if offload:
            model.cpu()
            torch.cuda.empty_cache()
            ae.decoder.to(x.device)

        # decode latents to pixel space
        x = unpack(x.float(), height, width)
        with torch.autocast(device_type=torch_device.type, dtype=torch.bfloat16):
            x = ae.decode(x)

        if torch.cuda.is_available():
            torch.cuda.synchronize()
        t1 = time.perf_counter()
        print(f"Done in {t1 - t0:.1f}s")

        idx = save_image(nsfw_classifier, name, output_name, idx, x, add_sampling_metadata, prompt)

        if loop:
            print("-" * 80)
            opts = parse_prompt(opts)
            opts = parse_img_cond_path(opts)
        else:
            opts = None


def app():
    Fire(main)


if __name__ == "__main__":
    app()
