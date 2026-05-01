import torch
import torchaudio
from transformers import AutoProcessor, AutoModelForSpeechSeq2Seq

device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"Device: {device}")

model_name = "ibm-granite/granite-4.0-1b-speech"
processor = AutoProcessor.from_pretrained(model_name)
model = AutoModelForSpeechSeq2Seq.from_pretrained(
    model_name, torch_dtype=torch.bfloat16, device_map=device
)
# Input WAV (replace)
input_wav = "/home/nalabotalaadvait/Documents/Dev1/ARCall-Entity-Extractor/local_data_source/Testing/Testing Transcripts/Example 12/9576826c-04d8-4b0f-913f-d9355454ecf4_5015_8002446224_03032026_084448.wav"
output_txt = "/home/nalabotalaadvait/Documents/Dev1/ARCall-Entity-Extractor/local_data_source/Testing/Testing Transcripts/Example 12/output.txt"
# Preprocess audio
wav, sr = torchaudio.load(input_wav)
if wav.shape[0] > 1: wav = torch.mean(wav, dim=0, keepdim=True)
if sr != 16000:
    torchaudio.transforms.Resample(sr, 16000)(wav)
wav = wav.to(device)

print(f"Audio: {wav.shape}")

text_prompt = "<|audio|>can you transcribe the speech into a written format?"
print(f"Prompt: {text_prompt}")

# Direct ASR: No text prompt
inputs = processor(wav, sampling_rate=16000, return_tensors="pt")
inputs = {k: v.to(device) for k, v in inputs.items()}
print(f"Inputs keys: {inputs.keys()}")

with torch.no_grad():
    generated_ids = model.generate(
        **inputs,
        max_new_tokens=256,
        do_sample=False,
        temperature=0.0,
        num_beams=4,  # Better quality
        pad_token_id=processor.tokenizer.pad_token_id or processor.tokenizer.eos_token_id
    )

# Decode full generation (processor handles forced BOS)
transcription = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
print(f"Transcription: {transcription}")

with open(output_txt, "w") as f:
    f.write(transcription.strip())