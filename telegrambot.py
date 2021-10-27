import re
import sys
import json
import torch
import logging
import argparse
import mysql.connector
from connector import request_db
from deanonymization import anonymization
from telegram import Update
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters, CallbackContext)
from transformers import GPT2Tokenizer, GPT2LMHeadModel

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

def parse_args():
    parser = argparse.ArgumentParser(description="Finetune a transformers "
                                    "model on a causal language modeling task")
    parser.add_argument("--checkpoint", type=str, default=None,
        help="A path for initial model.")
    parser.add_argument("--dialog_domain", type=str, default="consulta_saldo",
        help="Domain of possible dialogs with chatbot.")
    return parser.parse_args()

def initialize_table():
    mydb = mysql.connector.connect(host="remotemysql.com", user="fcjRTVuTI0", password="rTnUuTKbvQ", database="fcjRTVuTI0")
    create_table_dialogs = "CREATE TABLE IF NOT EXISTS dialogs (id BIGINT NOT NULL AUTO_INCREMENT, dialog_domain VARCHAR(256) NOT NULL, situation BOOLEAN NOT NULL, PRIMARY KEY (id))"
    create_table_turns = "CREATE TABLE IF NOT EXISTS turns (turn_num INT NOT NULL, id_dialog BIGINT NOT NULL, speaker VARCHAR(256) NULL, utterance VARCHAR(2048) NOT NULL, utterance_delex VARCHAR(2048) NOT NULL, intent_action VARCHAR(256) NOT NULL, PRIMARY KEY (id_dialog, turn_num), FOREIGN KEY (id_dialog) REFERENCES dialogs(id))"
    mycursor = mydb.cursor()

    mycursor.execute(create_table_dialogs)
    mydb.commit()

    mycursor.execute(create_table_turns)
    mydb.commit()

    mydb.close()

def insert_dialog(dialog_domain):
    mydb = mysql.connector.connect(host="remotemysql.com", user="fcjRTVuTI0", password="rTnUuTKbvQ", database="fcjRTVuTI0")
    insert_query = "INSERT INTO dialogs (dialog_domain, situation) VALUES (%s, %s)"
    values = (dialog_domain, 0)
    mycursor = mydb.cursor()
    mycursor.execute(insert_query, values)
    mydb.commit()
    id_dialog = mycursor.lastrowid
    mydb.close()
    return id_dialog

def insert_turn(id_dialog, speaker, utterance,
                utterance_delex, intent_action, turn_num):
    mydb = mysql.connector.connect(host="remotemysql.com", user="fcjRTVuTI0", password="rTnUuTKbvQ", database="fcjRTVuTI0")
    insert_query = "INSERT INTO turns (id_dialog, turn_num, speaker, utterance, utterance_delex, intent_action) VALUES (%s, %s, %s, %s, %s, %s)"
    values = (id_dialog, turn_num, speaker, utterance, utterance_delex, intent_action)
    mycursor = mydb.cursor()
    mycursor.execute(insert_query, values)
    mydb.commit()
    mydb.close()

def update_situation(id_dialog, situation):
    mydb = mysql.connector.connect(host="remotemysql.com", user="fcjRTVuTI0", password="rTnUuTKbvQ", database="fcjRTVuTI0")
    update_query = "UPDATE dialogs SET situation = "+str(situation)+" WHERE id = "+str(id_dialog)
    mycursor = mydb.cursor()
    mycursor.execute(update_query)
    mydb.commit()
    mydb.close()

def get_intents(sentence):
    result = "".join(re.compile(r'\[\S+\]').findall(sentence))
    return result

def telegram_bot(args):
    with open('telegram.json') as fin: api = json.load(fin)
    with torch.no_grad():
        tokenizer = GPT2Tokenizer.from_pretrained(args.checkpoint)
        model = GPT2LMHeadModel.from_pretrained(args.checkpoint)

        updater = Updater(token=api['token'])
        dispatcher = updater.dispatcher
        initialize_table()

        def start(update, context):
            #context.bot.send_message(chat_id=update.effective_chat.id,
            #                         text="Hi. I am a Ze Carioca, how can I help you?")
            response = "Olá. Eu sou o Ze Carioca, como eu posso te ajudar? "
            response += "Ao final avalie a nossa conversa, utilizando a tag /correct quando eu me comporto adequadamente "
            response += "e /incorrect quando o meu comportamento saiu do esperado. "
            response += "O domínio da nossa conversa é "+args.dialog_domain+"."
            context.bot.send_message(chat_id=update.effective_chat.id, text=response)

        def restart(update, context):
            #context.bot.send_message(chat_id=update.effective_chat.id,
            #                         text="Hi. I am a Ze Carioca, how can I help you?")
            response = "Olá. Eu sou o Ze Carioca, como eu posso te ajudar? "
            response += "Ao final avalie a nossa conversa, utilizando a tag /correct quando eu me comporto adequadamente "
            response += "e /incorrect quando o meu comportamento saiu do esperado. "
            response += "O domínio da nossa conversa é "+args.dialog_domain+"."
            context.bot.send_message(chat_id=update.effective_chat.id, text=response)
            if 'id' in context.user_data: context.user_data.pop('id')
            if 'variables' in context.user_data: context.user_data.pop('variables')
            if 'turn' in context.user_data: context.user_data.pop('turn')
            if 'msg' in context.user_data: context.user_data.pop('msg')

        def correct(update, context):
            if 'id' in context.user_data: update_situation(context.user_data['id'], 1)
            context.bot.send_message(chat_id=update.effective_chat.id,
                                     text="Diálogo correto adicionado com sucesso! Obrigada!")

        def incorrect(update, context):
            if 'id' in context.user_data: update_situation(context.user_data['id'], 0)
            context.bot.send_message(chat_id=update.effective_chat.id,
                                     text="Diálogo incorreto adicionado com sucesso! Obrigada!")

        def reply(update, context):
            msg = '<sos_u>'+update.message.text.lower()+'<eos_u>'
            msg = tokenizer.encode(msg, add_special_tokens=True)
            if 'id' not in context.user_data: context.user_data['id'] = insert_dialog(args.dialog_domain)
            if 'variables' not in context.user_data: context.user_data['variables'] = {}
            if 'turn' not in context.user_data: context.user_data['turn'] = 0
            if 'msg' not in context.user_data: context.user_data['msg'] = []
            contextmsg = context.user_data['msg'] + msg

            logging.info("[USER] "+tokenizer.decode(contextmsg))
            context_length = len(contextmsg)
            max_len=60

            outputs = model.generate(input_ids=torch.LongTensor(
                contextmsg).reshape(1,-1),
                max_length=context_length+max_len, temperature=0.7,
                pad_token_id=tokenizer.eos_token_id,
                eos_token_id=tokenizer.encode(['<eos_r>'])[0])
            generated = outputs[0].cpu().numpy().tolist()

            #variables = context.user_data['variables'].copy()
            decoded_output = tokenizer.decode(generated)
            user_response = update.message.text
            user_intent = get_intents(decoded_output.split('<sos_b>')[-1].split('<eos_b>')[0])
            #user_delex, user_response, variables = anonymization(user_response, variables, False)
            insert_turn(context.user_data['id'], "client", user_response, user_response,
                        user_intent, context.user_data['turn'])
            context.user_data['turn'] += 1

            system_response = decoded_output.split('<sos_r>')[-1].split('<eos_r>')[0]
            system_action = get_intents(decoded_output.split('<sos_a>')[-1].split('<eos_a>')[0])
            #system_delex, system_response, variables = anonymization(system_response, variables, False)
            #valid, system_response, new_action = request_db(args.dialog_domain, system_delex, variables)
            #if (valid): context.user_data['msg'] = contextmsg + tokenizer.encode(system_response, add_special_tokens=True)
            #else: system_action = new_action
            #context.user_data['variables'] = variables
            insert_turn(context.user_data['id'], "agent", system_response, system_response,
                        system_action, context.user_data['turn'])
            context.user_data['turn'] += 1

            print(generated)
            print("="*80)
            print(decoded_output)
            #print(parse_data(decoded_output))
            logging.info("[SYSTEM] "+decoded_output)
            context.bot.send_message(chat_id=update.effective_chat.id, text=system_response)

        start_handler = CommandHandler('start', start)
        dispatcher.add_handler(start_handler)
        restart_handler = CommandHandler('restart', restart)
        dispatcher.add_handler(restart_handler)
        correct_handler = CommandHandler('correct', correct)
        dispatcher.add_handler(correct_handler)
        incorrect_handler = CommandHandler('incorrect', incorrect)
        dispatcher.add_handler(incorrect_handler)
        reply_handler = MessageHandler(Filters.text & (~Filters.command), reply)
        dispatcher.add_handler(reply_handler)

        updater.start_polling()
        updater.idle()

if __name__ == "__main__":
    args = parse_args()
    telegram_bot(args)