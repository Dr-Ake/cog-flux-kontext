# FLUX.1 Kontext

This repository wraps the **FLUX.1 Kontext** image‑to‑image model in a simple [Cog](https://github.com/replicate/cog) predictor. It loads the transformer, autoencoder and text encoders and exposes a single `predict` function for applying edits or style transfer to an input image.

FLUX.1 Kontext is an experimental model from Black Forest Labs. The code in this repository is licensed under Apache‑2.0. See the model card for FLUX.1’s own licence.

## Requirements

This repository targets Python 3.11 and PyTorch 2.7.1 and requires a CUDA
enabled GPU (CUDA 12.6 or later). All Python dependencies are listed in
`requirements.txt`.

## Quick start

The easiest way to run the model is with Cog.  First install Cog and then run:

```bash
cog predict -i prompt="make the hair blue" -i input_image=@lady.png
```

All required model weights are downloaded from Replicate the first time you run
the predictor and cached under `models/`. The predictor uses `torch.compile`
and stores the compiled model so subsequent runs are faster.

## Running the demo UI

A small [Gradio](https://gradio.app) demo is provided in `app.py`.  Launch it with:

```bash
python app.py
```

This starts a local web interface where you can experiment with different
prompts, aspect ratios and other options without using Cog directly.

## Predictor options

When calling `predict.py` (either via Cog or from another Python script) you can
control several parameters:

- `prompt` – text instruction describing how to modify the input image
- `input_image` – path to the source image (jpg, png, gif or webp)
- `aspect_ratio` – aspect ratio of the output. `match_input_image` keeps the
  original ratio
- `num_inference_steps` – number of denoising steps (4–50)
- `guidance` – guidance scale controlling prompt strength
- `seed` – optional random seed for repeatable results
- `output_format` – one of `webp`, `jpg` or `png`
- `output_quality` – quality value for jpg/webp outputs
- `disable_safety_checker` – skip NSFW filtering
- `go_fast` – enable the Taylor‐seer style cache for faster but potentially
  lower quality output

Calling the predictor returns the path to the generated image.

## Precompiling Torch code

The script `generate_torch_compile_cache.py` builds and saves a
`torch.compile` cache (`torch-compile-cache-flux-dev-kontext.bin`) so the first
inference call is faster.  Running the predictor without this cache will cause
it to be generated automatically but at the cost of a slower initial run.

## License

The wrapper code in this repository is released under the Apache‑2.0 license.
Model weights are provided by Black Forest Labs and are subject to their own
terms.
