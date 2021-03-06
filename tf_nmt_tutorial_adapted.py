#!/usr/bin/env python

# Adapted from https://www.tensorflow.org/tutorials/text/nmt_with_attention

import tensorflow as tf
from tensorflow.compat.v1.keras.backend import set_session
from tensorflow.compat.v1 import ConfigProto
from tensorflow.compat.v1 import InteractiveSession

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from sklearn.model_selection import train_test_split

import sqlite3

import unicodedata
import re
import numpy as np
import os
import io
import time
import tokenize as tokenize_lib

# There's two because I'm using two computer,
# you can ignore the second one
DB_FILE = '/home/jcp353/all_data.db'
DB_FILE2 = '/home/HDD/code_and_comments/all_data.db'
# Number of python code comment pairs
NUM_EXAMPLES = 25000
# Training evaluation split
SPLIT = int(NUM_EXAMPLES * 0.8)
# A cap on the length of the python code and comment
# examples. All those chosen will be less than or
# equal to this many characters.
EXAMPLE_LENGTH_CAP = 300
EPOCHS = 50
BATCH_SIZE = 128
# Keeps only this many of the most common words,
# everything else will qualify as unknown
NUM_WORDS = 10000
# Sets how much of the GPU to use, I was having
# trouble at 1.0 but you can set it to that if
# that works for you
MEM_FRAC = 0.98
# Neural net dimensions/units
EMBEDDING_DIM = 256
UNITS = 1024

key_words = ['False','await','else','import','pass',
            'None','break','except','in','raise',
            'True','class','finally','is','return',
            'and','continue','for','lambda','try',
            'as','def','from','nonlocal','while',
            'assert','del','global','not','with',
            'async','elif','if','or','yield']

# Converts the unicode file to ascii
def unicode_to_ascii(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn')

def preprocess_sentence(w):
    w = unicode_to_ascii(w.lower().strip())

    # creating a space between a word and the punctuation following it
    # eg: "he is a boy." => "he is a boy ."
    # Reference:- https://stackoverflow.com/questions/3645931/python-padding-punctuation-with-white-spaces-keeping-punctuation
    w = re.sub(r"([?.!,¿();=:])", r" \1 ", w)
    w = re.sub(r'[" "]+', " ", w)

    # replacing everything not listed below with space
    w = re.sub(r"[^a-zA-Z?.!,¿();=:]+", " ", w)

    w = w.strip()

    # adding a start and an end token to the sentence
    # so that the model know when to start and stop predicting.
    w = '<start> ' + w + ' <end>'
    return w

def tokenize_python(code_snippet, genaraic_vars = False, fail=True):
    tokens = tokenize_lib.tokenize(io.BytesIO(code_snippet.encode('utf-8')).readline)
    if not fail:
        tokens2 = []
        while True:
            try:
                tokens2.append(tokens.__next__())
            except:
                break
    else:
        tokens2 = tokens
    parsed = []
    parsed.append('<start>')

    #for keeping track of variables
    variables = []
    var_count=0
    try:
        for token in tokens2:

            if token.type not in [0,57,58,59,60,61,62,63,256]:
                #keyword or variable
                if token.type == 1 and genaraic_vars:

                    #key word
                    if token.string in key_words:
                        parsed.append(token.string)

                    #variable
                    else:
                        #new var
                        if token.string not in variables:
                            var_count+=1
                            parsed.append('var_'+str(var_count))
                            variables.append(token.string)
                        else:
                            parsed.append('var_'+str(variables.index(token.string)))


                #string
                elif token.type == 2:
                    parsed.append('<number>')

                #number
                elif token.type == 3:
                    parsed.append('<string>')

                #everything else
                else:
                    parsed.append(token.string)


        parsed.append('<end>')
        return parsed
    except:
        return None

def tokenize(lang):
    lang_tokenizer = tf.keras.preprocessing.text.Tokenizer(
            filters=' ', num_words=NUM_WORDS)
    lang_tokenizer.fit_on_texts(lang)

    tensor = lang_tokenizer.texts_to_sequences(lang)

    tensor = tf.keras.preprocessing.sequence.pad_sequences(tensor, padding='post')

    return tensor, lang_tokenizer

def load_dataset(targ_lang, inp_lang, num_examples=NUM_EXAMPLES):
    # creating cleaned input, output pairs

    input_tensor, inp_lang_tokenizer = tokenize(inp_lang)
    target_tensor, targ_lang_tokenizer = tokenize(targ_lang)

    return input_tensor, target_tensor, inp_lang_tokenizer, targ_lang_tokenizer

def convert(lang, tensor):
    for t in tensor:
        if t!=0:
            print ("%d ----> %s" % (t, lang.index_word[t]))

class Encoder(tf.keras.Model):
    def __init__(self, vocab_size, embedding_dim, enc_units, batch_sz):
        super(Encoder, self).__init__()
        self.batch_sz = batch_sz
        self.enc_units = enc_units
        self.embedding = tf.keras.layers.Embedding(vocab_size, embedding_dim)
        self.gru = tf.keras.layers.GRU(self.enc_units,
                 return_sequences=True,
                 return_state=True,
                 recurrent_initializer='glorot_uniform')

    def call(self, x, hidden):
        x = self.embedding(x)
        output, state = self.gru(x, initial_state = hidden)
        return output, state

    def initialize_hidden_state(self):
        return tf.zeros((self.batch_sz, self.enc_units))

class BahdanauAttention(tf.keras.layers.Layer):
    def __init__(self, units):
        super(BahdanauAttention, self).__init__()
        self.W1 = tf.keras.layers.Dense(units)
        self.W2 = tf.keras.layers.Dense(units)
        self.V = tf.keras.layers.Dense(1)

    def call(self, query, values):
        # query hidden state shape == (batch_size, hidden size)
        # query_with_time_axis shape == (batch_size, 1, hidden size)
        # values shape == (batch_size, max_len, hidden size)
        # we are doing this to broadcast addition along the time axis to calculate the score
        query_with_time_axis = tf.expand_dims(query, 1)

        # score shape == (batch_size, max_length, 1)
        # we get 1 at the last axis because we are applying score to self.V
        # the shape of the tensor before applying self.V is (batch_size, max_length, units)
        score = self.V(tf.nn.tanh(
                self.W1(query_with_time_axis) + self.W2(values)))

        # attention_weights shape == (batch_size, max_length, 1)
        attention_weights = tf.nn.softmax(score, axis=1)

        # context_vector shape after sum == (batch_size, hidden_size)
        context_vector = attention_weights * values
        context_vector = tf.reduce_sum(context_vector, axis=1)

        return context_vector, attention_weights

class Decoder(tf.keras.Model):
    def __init__(self, vocab_size, embedding_dim, dec_units, batch_sz):
        super(Decoder, self).__init__()
        self.batch_sz = batch_sz
        self.dec_units = dec_units
        self.embedding = tf.keras.layers.Embedding(vocab_size, embedding_dim)
        self.gru = tf.keras.layers.GRU(self.dec_units,
                 return_sequences=True,
                 return_state=True,
                 recurrent_initializer='glorot_uniform')
        self.fc = tf.keras.layers.Dense(vocab_size)

        # used for attention
        self.attention = BahdanauAttention(self.dec_units)

    def call(self, x, hidden, enc_output):
        # enc_output shape == (batch_size, max_length, hidden_size)
        context_vector, attention_weights = self.attention(hidden, enc_output)

        # x shape after passing through embedding == (batch_size, 1, embedding_dim)
        x = self.embedding(x)

        # x shape after concatenation == (batch_size, 1, embedding_dim + hidden_size)
        x = tf.concat([tf.expand_dims(context_vector, 1), x], axis=-1)

        # passing the concatenated vector to the GRU
        output, state = self.gru(x)

        # output shape == (batch_size * 1, hidden_size)
        output = tf.reshape(output, (-1, output.shape[2]))

        # output shape == (batch_size, vocab)
        x = self.fc(output)

        return x, state, attention_weights

def loss_function(real, pred):
    mask = tf.math.logical_not(tf.math.equal(real, 0))
    loss_ = loss_object(real, pred)

    mask = tf.cast(mask, dtype=loss_.dtype)
    loss_ *= mask

    return tf.reduce_mean(loss_)

@tf.function
def train_step(inp, targ, enc_hidden):
    loss = 0

    with tf.GradientTape() as tape:
        enc_output, enc_hidden = encoder(inp, enc_hidden)

        dec_hidden = enc_hidden

        dec_input = tf.expand_dims([targ_lang.word_index['<start>']] * BATCH_SIZE, 1)

        # Teacher forcing - feeding the target as the next input
        for t in range(1, targ.shape[1]):
            # passing enc_output to the decoder
            predictions, dec_hidden, _ = decoder(dec_input, dec_hidden, enc_output)

            loss += loss_function(targ[:, t], predictions)

            # using teacher forcing
            dec_input = tf.expand_dims(targ[:, t], 1)

    batch_loss = (loss / int(targ.shape[1]))

    variables = encoder.trainable_variables + decoder.trainable_variables

    gradients = tape.gradient(loss, variables)

    optimizer.apply_gradients(zip(gradients, variables))

    return batch_loss


# ## Translate
# 
# * The evaluate function is similar to the training loop, except we don't use
#    *teacher forcing* here. The input to the decoder at each time step is its
#    previous predictions along with the hidden state and the encoder output.
# * Stop predicting when the model predicts the *end token*.
# * And store the *attention weights for every time step*.
# 
# Note: The encoder output is calculated only once for one input.

def evaluate(sentence):
    attention_plot = np.zeros((max_length_targ, max_length_inp))

    sentence = tokenize_python(sentence, fail=False)
    keys = inp_lang.word_index.keys()
    sentence = [' ' if x not in keys else x for x in sentence]
    inputs = [inp_lang.word_index[i] for i in sentence]
    inputs = tf.keras.preprocessing.sequence.pad_sequences([inputs],
             maxlen=max_length_inp,
             padding='post')
    inputs = tf.convert_to_tensor(inputs)

    result = ''

    hidden = [tf.zeros((1, units))]
    enc_out, enc_hidden = encoder(inputs, hidden)

    dec_hidden = enc_hidden
    dec_input = tf.expand_dims([targ_lang.word_index['<start>']], 0)

    for t in range(max_length_targ):
        predictions, dec_hidden, attention_weights = decoder(dec_input,
                dec_hidden, enc_out)

        # storing the attention weights to plot later on
        attention_weights = tf.reshape(attention_weights, (-1, ))
        attention_plot[t] = attention_weights.numpy()

        predicted_id = tf.argmax(predictions[0]).numpy()

        result += targ_lang.index_word[predicted_id] + ' '

        if targ_lang.index_word[predicted_id] == '<end>':
            return result, sentence, attention_plot

        # the predicted ID is fed back into the model
        dec_input = tf.expand_dims([predicted_id], 0)

    return result, sentence, attention_plot

# function for plotting the attention weights
def plot_attention(attention, sentence, predicted_sentence):
    # Uncomment next line to disable
    #return None
    fig = plt.figure(figsize=(10,10))
    ax = fig.add_subplot(1, 1, 1)
    ax.matshow(attention, cmap='viridis')

    fontdict = {'fontsize': 14}

    ax.set_xticklabels([''] + sentence, fontdict=fontdict, rotation=90)
    ax.set_yticklabels([''] + predicted_sentence, fontdict=fontdict)

    ax.xaxis.set_major_locator(ticker.MultipleLocator(1))
    ax.yaxis.set_major_locator(ticker.MultipleLocator(1))

    plt.show()
    # Uncomment this line to save as a file instead of showing
    #plt.savefig('figure.png')

def translate(sentence):
    result, sentence, attention_plot = evaluate(sentence)

    print('Input: %s' % (sentence))
    print('Predicted translation: {}'.format(result))

    attention_plot = attention_plot[:len(result.split(' ')), :len(sentence)]
    plot_attention(attention_plot, sentence, result.split(' '))


def main():
    # Wouldn't normally do this but don't want to rewrite everything
    global encoder, targ_lang, decoder, loss_object, optimizer,\
            max_length_targ, max_length_inp, inp_lang, enc_hidden,\
            units
    # Lazy workaround for 2 computers
    try:
        conn = sqlite3.connect(DB_FILE)
    except:
        conn = sqlite3.connect(DB_FILE2)
    c = conn.cursor()
    # Filter down to short-ish python examples
    pairs = c.execute('SELECT * FROM all_data WHERE filename LIKE "%.py" AND ' +
                    'length(code) < {0} AND length(comment) < {0} LIMIT {1}'.format(
                            EXAMPLE_LENGTH_CAP, NUM_EXAMPLES))
    pairs = [i[1:] for i in pairs.fetchall()]

    source_sentence = pairs[0][0]
    target_sentence = pairs[0][1]
    print(preprocess_sentence(target_sentence))
    print(preprocess_sentence(source_sentence).encode('utf-8'))

    source = [tokenize_python(i[0]) for i in pairs]
    target = [preprocess_sentence(i[1]) for i in pairs]
    # Very quick and dirty removal of errored input
    filter_out = [i for i,y in enumerate(source) if y is None]
    for i in range(len(source)):
        if i in filter_out:
            target[i] = None
    source = list(filter(None, source))
    target = list(filter(None, target))
    input_tensor, target_tensor, inp_lang, targ_lang = load_dataset(target, source, NUM_EXAMPLES)

    # Calculate max_length of the target tensors
    max_length_targ, max_length_inp = target_tensor.shape[1], input_tensor.shape[1]

    # Creating training and validation sets using an 80-20 split
    input_tensor_train = input_tensor[:SPLIT]
    input_tensor_val = input_tensor[SPLIT:]
    target_tensor_train = target_tensor[:SPLIT]
    target_tensor_val = target_tensor[SPLIT:]

    # Show length
    print(len(input_tensor_train), len(target_tensor_train), len(input_tensor_val),
            len(target_tensor_val))

    print ("Input Language; index to word mapping")
    convert(inp_lang, input_tensor_train[0])
    print ()
    print ("Target Language; index to word mapping")
    convert(targ_lang, target_tensor_train[0])

    # Limit GPU memory if relevant
    config = ConfigProto()
    config.gpu_options.per_process_gpu_memory_fraction = MEM_FRAC
    config.gpu_options.allow_growth = True
    session = InteractiveSession(config=config)

    BUFFER_SIZE = len(input_tensor_train)
    steps_per_epoch = len(input_tensor_train)//BATCH_SIZE
    embedding_dim = EMBEDDING_DIM
    units = UNITS
    vocab_inp_size = len(inp_lang.word_index)+1
    vocab_tar_size = len(targ_lang.word_index)+1

    dataset = tf.data.Dataset.from_tensor_slices((input_tensor_train, target_tensor_train)).shuffle(BUFFER_SIZE)
    dataset = dataset.batch(BATCH_SIZE, drop_remainder=True)

    example_input_batch, example_target_batch = next(iter(dataset))
    example_input_batch.shape, example_target_batch.shape

    encoder = Encoder(vocab_inp_size, embedding_dim, units, BATCH_SIZE)

    # sample input
    sample_hidden = encoder.initialize_hidden_state()
    sample_output, sample_hidden = encoder(example_input_batch, sample_hidden)
    print ('Encoder output shape: (batch size, sequence length, units) {}'.format(sample_output.shape))
    print ('Encoder Hidden state shape: (batch size, units) {}'.format(sample_hidden.shape))

    attention_layer = BahdanauAttention(10)
    attention_result, attention_weights = attention_layer(sample_hidden, sample_output)

    print("Attention result shape: (batch size, units) {}".format(attention_result.shape))
    print("Attention weights shape: (batch_size, sequence_length, 1) {}".format(attention_weights.shape))

    decoder = Decoder(vocab_tar_size, embedding_dim, units, BATCH_SIZE)

    sample_decoder_output, _, _ = decoder(tf.random.uniform((BATCH_SIZE, 1)),
                                                                                sample_hidden, sample_output)

    print ('Decoder output shape: (batch_size, vocab size) {}'.format(sample_decoder_output.shape))

    # ## Define the optimizer and the loss function

    optimizer = tf.keras.optimizers.Adam()
    loss_object = tf.keras.losses.SparseCategoricalCrossentropy(
            from_logits=True, reduction='none')


    # ## Checkpoints (Object-based saving)

    checkpoint_dir = './training_checkpoints'
    checkpoint_prefix = os.path.join(checkpoint_dir, "ckpt")
    checkpoint = tf.train.Checkpoint(optimizer=optimizer,
            encoder=encoder, decoder=decoder)

    # The following section trains the model, if you want to load
    # an already trained model comment it out
    ##########################TRAINING##############
    for epoch in range(EPOCHS):
        start = time.time()

        enc_hidden = encoder.initialize_hidden_state()
        total_loss = 0

        for (batch, (inp, targ)) in enumerate(dataset.take(steps_per_epoch)):
            batch_loss = train_step(inp, targ, enc_hidden)
            total_loss += batch_loss

            if batch % 100 == 0:
                print('Epoch {} Batch {} Loss {:.4f}'.format(epoch + 1, batch,
                    batch_loss.numpy()))
        # saving (checkpoint) the model every 2 epochs
        if (epoch + 1) % 2 == 0:
            checkpoint.save(file_prefix = checkpoint_prefix)

        print('Epoch {} Loss {:.4f}'.format(epoch + 1,
            total_loss / steps_per_epoch))
        print('Time taken for 1 epoch {} sec\n'.format(time.time() - start))
    # restoring the latest checkpoint in checkpoint_dir
    ##########################TRAINING##############
    checkpoint.restore(tf.train.latest_checkpoint(checkpoint_dir))

    # Prints the results for 100 testing samples
    for i in range(SPLIT+1, SPLIT+101):
        sent = pairs[i][0]
        translate(sent)

if __name__ == '__main__':
    main()
