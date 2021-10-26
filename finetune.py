#!/usr/bin/env python
# coding=utf-8
import sys
import math
import json
import torch
import logging
import argparse
from tqdm import tqdm
from copy import deepcopy
from dialogparser import parser
from evaluate import compute
from torch.utils.data import Dataset, DataLoader
from transformers import (
    GPT2Tokenizer, GPT2LMHeadModel,
    default_data_collator,
    AdamW,
    get_scheduler,
    SchedulerType
)
from datasets import load_dataset, load_metric

torch.manual_seed(42)

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

def compute_metrics(pred):
    preds, labels = pred
    preds = parser(tokenizer.decode(preds))
    labels = parser(tokenizer.decode(labels))
    return compute(preds, labels)

def parse_args():
    parser = argparse.ArgumentParser(description="Finetune a transformers "
                                    "model on a causal language modeling task")
    parser.add_argument("--checkpoint", type=str, default=None,
        help="A path for initial model.")
    parser.add_argument("--batch_size", type=int, default=32,
        help="Size of the batch.")
    parser.add_argument("--train_file", type=str, default=None,
        help="A json file containing the training data.")
    parser.add_argument("--validation_file", type=str, default=None,
        help="A json file containing the validation data.")
    parser.add_argument("--learning_rate", type=float, default=2e-5,
        help="Initial learning rate to use.")
    parser.add_argument("--weight_decay", type=float, default=0.01,
        help="Weight decay to use.")
    parser.add_argument("--num_train_epochs", type=int, default=20,
        help="Total number of training epochs to perform.")
    parser.add_argument("--max_train_steps", type=int, default=None,
        help="Total number of training steps to perform.")
    parser.add_argument("--gradient_accumulation_steps", type=int, default=1,
        help="Number of updates steps to accumulate before performing a backward/update pass.")
    parser.add_argument("--lr_scheduler_type", type=SchedulerType, default="linear",
        help="The scheduler type to use.",
        choices=["linear", "cosine", "cosine_with_restarts", "polynomial", "constant", "constant_with_warmup"])
    parser.add_argument("--num_warmup_steps", type=int, default=200,
        help="Number of steps for the warmup in the lr scheduler.")
    return parser.parse_args()

def main():
    args = parse_args()

    with open("data/ontology.json") as fin:
        tokens = json.load(fin)

    tokenizer = GPT2Tokenizer.from_pretrained(args.checkpoint)
    model = GPT2LMHeadModel.from_pretrained(args.checkpoint)
    tokenizer.added_tokens_encoder = {}
    tokenizer.added_tokens_dencoder = {}
    tokenizer.add_special_tokens({'additional_special_tokens': tokens})
    tokenizer.save_pretrained("models/tokenizer/")
    tokenizer.pad_token = tokenizer.eos_token
    model.resize_token_embeddings(len(tokenizer))
    datasets = load_dataset("json", data_files={"train":args.train_file,
                                                "valid":args.validation_file})
    def add_tokens(examples):
        res = tokenizer(examples['text'], max_length=256, truncation=True,
                        padding='max_length')
        res['labels'] = deepcopy(res['input_ids'])
        return res

    tokenized = datasets.map(add_tokens, num_proc=4, batched=True,
                             batch_size=args.batch_size,
                             remove_columns=["id", "text", "mwoz"])

    train_dataset = tokenized["train"]
    valid_dataset = tokenized["valid"]
    train_dataloader = DataLoader(
        train_dataset, shuffle=True, batch_size=args.batch_size,
        collate_fn=default_data_collator
    )
    valid_dataloader = DataLoader(
        valid_dataset, batch_size=args.batch_size,
        collate_fn=default_data_collator
    )
    no_decay = ["bias", "LayerNorm.weight"]
    optimizer_grouped_parameters = [
        {
            "params": [p for n, p in model.named_parameters() if not any(nd in n for nd in no_decay)],
            "weight_decay": args.weight_decay,
        },
        {
            "params": [p for n, p in model.named_parameters() if any(nd in n for nd in no_decay)],
            "weight_decay": 0.0,
        },
    ]
    optimizer = AdamW(optimizer_grouped_parameters, lr=args.learning_rate)

    num_update_steps_per_epoch = math.ceil(len(train_dataloader) / args.gradient_accumulation_steps)
    if args.max_train_steps is None:
        args.max_train_steps = args.num_train_epochs * num_update_steps_per_epoch
    else:
        args.num_train_epochs = math.ceil(args.max_train_steps / num_update_steps_per_epoch)

    lr_scheduler = get_scheduler(
        name=args.lr_scheduler_type,
        optimizer=optimizer,
        num_warmup_steps=args.num_warmup_steps,
        num_training_steps=args.max_train_steps,
    )

    total_batch_size = args.batch_size * args.gradient_accumulation_steps

    logger.info("***** Running training *****")
    logger.info(f"  Num examples = {len(train_dataset)}")
    logger.info(f"  Num Epochs = {args.num_train_epochs}")
    logger.info(f"  Instantaneous batch size per device = {args.batch_size}")
    logger.info(f"  Gradient Accumulation steps = {args.gradient_accumulation_steps}")
    logger.info(f"  Total train batch size (w. parallel, distributed & accumulation) = {total_batch_size}")
    logger.info(f"  Total optimization steps = {args.max_train_steps}")
    # Only show the progress bar once on each machine.
    progress_bar = tqdm(range(args.max_train_steps))
    completed_steps = 0

    device = "cuda:0"
    model.to(device)
    for epoch in range(args.num_train_epochs):
        model.train()
        for step, batch in enumerate(train_dataloader):
            batch = {k: v.to(device) for k, v in batch.items()}
            outputs = model(**batch)
            loss = outputs.loss
            # loss = loss / args.gradient_accumulation_steps
            loss.backward()
            if step % args.gradient_accumulation_steps == 0 or step == len(train_dataloader) - 1:
                optimizer.step()
                lr_scheduler.step()
                optimizer.zero_grad()
                progress_bar.update(1)
                completed_steps += 1

            if completed_steps >= args.max_train_steps:
                break

        model.eval()
        losses = []
        preds = []
        labes = []
        for step, batch in enumerate(valid_dataloader):
            with torch.no_grad():
                batch = {k: v.to(device) for k, v in batch.items()}
                outputs = model(**batch)
            logits = outputs.logits
            loss = outputs.loss
            losses.append(loss.item())
            for pred in torch.argmax(logits, dim=-1).cpu():
                preds.append(parser(tokenizer.decode(pred)))
            for lab in batch["labels"].cpu():
                labes.append(parser(tokenizer.decode(lab)))
        try:
            losses = torch.tensor(losses)
            mloss = torch.mean(losses)
        except OverflowError:
            perplexity = float("inf")
        print(compute(preds, labes))
        logger.info(f"epoch {epoch}: loss: {mloss}")

        # model.save_pretrained(args.output_dir)
        # tokenizer.save_pretrained(args.output_dir)

if __name__ == "__main__":
    main()
