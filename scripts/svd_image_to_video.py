#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a short image-to-video MP4 with Stable Video Diffusion.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--prompt-file", default="")  # accepted for the generic VideoGen command contract
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--width", type=int, default=768)
    parser.add_argument("--height", type=int, default=432)
    parser.add_argument("--model", default="stabilityai/stable-video-diffusion-img2vid-xt")
    parser.add_argument("--fps", type=int, default=7)
    parser.add_argument("--motion-bucket-id", type=int, default=127)
    parser.add_argument("--noise-aug-strength", type=float, default=0.02)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        import torch
        from diffusers import StableVideoDiffusionPipeline
        from diffusers.utils import export_to_video, load_image
    except ImportError as exc:
        raise SystemExit(
            "Stable Video Diffusion dependencies are missing. Install torch, diffusers, transformers, accelerate and imageio[ffmpeg]."
        ) from exc

    source = Path(args.input).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"Input image not found: {source}")
    output.parent.mkdir(parents=True, exist_ok=True)

    # SVD is trained around a 16:9 image. Resize/crop the reference before feeding
    # the model; the final VideoGen/OpenMontage render can scale it to the project profile.
    image = load_image(str(source))
    image = image.resize((1024, 576))

    dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    pipe = StableVideoDiffusionPipeline.from_pretrained(
        args.model,
        torch_dtype=dtype,
        variant="fp16" if dtype == torch.float16 else None,
    )

    if torch.cuda.is_available():
        # CPU offload + forward chunking are deliberately conservative for the
        # current 6-8 GB class GPU. A future larger GPU can disable offload in a
        # custom runner without changing VideoGen's motion API.
        pipe.enable_model_cpu_offload()
        pipe.unet.enable_forward_chunking()
    else:
        pipe.to("cpu")

    generator_device = "cuda" if torch.cuda.is_available() else "cpu"
    generator = torch.Generator(device=generator_device).manual_seed(args.seed)
    frames = pipe(
        image,
        decode_chunk_size=2,
        generator=generator,
        motion_bucket_id=args.motion_bucket_id,
        noise_aug_strength=args.noise_aug_strength,
        num_frames=25,
    ).frames[0]

    # 25 frames at 7 fps is ~3.6 s. Keep the native cadence for stability; the
    # OpenMontage scene timeline may hold/fill around the clip as needed.
    export_to_video(frames, str(output), fps=args.fps)
    if not output.is_file() or output.stat().st_size == 0:
        raise SystemExit("SVD did not create an output video")
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
