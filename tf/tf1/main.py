import sys, os, os.path, time
import pickle, re
import argparse
import numpy as np
from fileutils.kaldi import readArk
import tf

def load_haitian_label(DATA_DIR):
    labels = {}
    # Maps 2 to 0 and 7~40 to 1~34
    mapLabel = lambda x: 0 if x == 2 else x - 6
    for filename in ["labels.tr", "labels.cv"]:
        with open(os.path.join(DATA_DIR, filename), "r") as f:
            for line in f:
                tokens = line.strip().split()
                labels[tokens[0]] = [mapLabel(int(x)) for x in tokens[1:]]
    return 36, labels

def load_swbd_label(DATA_DIR):
    labels = {}
    # Maps 1 to 45 to 0 to 44 
    mapLabel = lambda x: x - 1
    for filename in ["labels.tr", "labels.cv"]:
        with open(os.path.join(DATA_DIR, filename), "r") as f:
            for line in f:
                tokens = line.strip().split()
                labels[tokens[0]] = [mapLabel(int(x)) for x in tokens[1:]]
    return 46, labels

def get_batch(feats, labels, start, height):
    max_feat_len = max(len(feats[start+i]) for i in range(height))
    max_label_len = max(len(labels[start+i]) for i in range(height))
    tmpx = np.zeros((height, max_feat_len, feats[start].shape[-1]), np.float32)
    yshape = np.array([height, max_label_len], dtype = np.int32)
    yidx, yval = [], []
    for i in range(height):
        feat, label = feats[start+i], labels[start+i]
        tmpx[i, :len(feat), :] = feat
        for j in range(len(label)):
            yidx.append([i, j])
            yval.append(label[j])

    yidx = np.asarray(yidx, dtype = np.int32)
    yval = np.asarray(yval, dtype = np.int32)
    return tmpx, yidx, yval, yshape

def make_batches(feats, labels, uttids, BATCH_SIZE):
    batch_x, batch_y = [], []
    L = len(feats)
    feats, labels, uttids = zip(*sorted(zip(feats, labels, uttids), key = lambda x: x[0].shape[0]))
    for start in range(0, L, BATCH_SIZE):
        height = min(BATCH_SIZE, L - start)
        tmpx, yidx, yval, yshape = get_batch(feats, labels, start, height)
        batch_x.append(tmpx)
        batch_y.append((yidx, yval, yshape))
    return batch_x, batch_y, uttids

def make_even_batches(feats, labels, uttids, BATCH_SIZE):
    """
    CudnnLSTM requires batches of even sizes
    """
    batch_x, batch_y = [], []
    L = len(feats)
    feats, labels, uttids = zip(*sorted(zip(feats, labels, uttids), key = lambda x: x[0].shape[0]))
    idx = 0
    while idx < L:
        sys.stdout.flush()
        # find batch with even size, and with maximum size of BATCH_SIZE
        j = idx + 1
        target_len = feats[idx].shape[0]
        while j < min(idx + BATCH_SIZE, L) and feats[j].shape[0] == target_len: 
            j += 1
        tmpx, yidx, yval, yshape = get_batch(feats, labels, idx, j - idx)
        batch_x.append(tmpx)
        batch_y.append((yidx, yval, yshape))
        idx = j
    return batch_x, batch_y, uttids

def load_feat(args, part):
    use_cudnn = args.use_cudnn
    DATA_DIR = args.data_dir
    BATCH_SIZE = args.batch_size
    if args.dataset == 'swbd':
        nclass, label_dict = load_swbd_label(DATA_DIR)
    elif args.dataset == 'haitian':
        nclass, label_dict = load_haitian_label(DATA_DIR)

    x, y = None, None
    features, labels, uttids = [], [], []
    files = [f for f in os.listdir(DATA_DIR) if re.match(part + "\d.ark", f)]
    nfile = len(files)
    for i in range(nfile):
        filename = os.path.join(DATA_DIR, "%s%d.ark" % (part, i))
        print("Reading file:", filename)
        sys.stdout.flush()
        part_features, part_uttids = readArk(filename)
        # part_features, part_uttids = readArk(filename, 1000)
        part_labels = [label_dict["%dx%s" % (i, x)] for x in part_uttids]
        features += part_features
        labels += part_labels
        uttids += part_uttids

    if use_cudnn: 
        x, y, uttids = make_even_batches(features, labels, uttids, BATCH_SIZE)
    else:
        x, y, uttids = make_batches(features, labels, uttids, BATCH_SIZE)
    return nclass, (x, y, uttids) 

def load_prior(prior_path):
    prior = None
    with open(prior_path, "r") as f:
        for line in f:
            parts = map(int, line.split(" ")[1:-1])
            counts = parts[1:]
            counts.append(parts[0])
            cnt_sum = reduce(lambda x, y: x + y, counts)
            prior = [float(x) / cnt_sum for x in counts]
    return prior

def get_output_folder(parent_dir):
    exp_name = "dbr"
    if not os.path.exists(parent_dir):
        os.makedirs(parent_dir)
    experiment_id = 0
    for folder_name in os.listdir(parent_dir):
        if not os.path.isdir(os.path.join(parent_dir, folder_name)):
            continue
        try:
            folder_name = int(folder_name.split('-run')[-1])
            if folder_name > experiment_id:
                experiment_id = folder_name
        except:
            pass
    experiment_id += 1

    parent_dir = os.path.join(parent_dir, exp_name)
    parent_dir = parent_dir + '-run{}'.format(experiment_id)
    return parent_dir

def main():
    parser = argparse.ArgumentParser(description='run deep bidrectional rnn')
    parser.add_argument('--use_cudnn', default=False, dest='use_cudnn', action='store_true', help='use cudnn lstm')
    parser.add_argument('--store_model', default=False, dest='store_model', action='store_true', help='store model')
    parser.add_argument('--eval', default=False, dest='eval', action='store_true', help='enable evaluation mode')
    parser.add_argument('--eval_model', default = "", help = "model to load for evaluation")
    parser.add_argument('--batch_size', default = 32, type=int, help='batch size')
    parser.add_argument('--data_dir', default = "/data/ASR5/fmetze/eesen/asr_egs/swbd/v1/tmp.LHhAHROFia/T22/", help = "data dir")
    parser.add_argument('--count_dir', default = "/data/ASR5/fmetze/eesen/asr_egs/swbd/v1/label.counts", help = "data dir")
    parser.add_argument('--nepoch', default = 30, type=int, help='#epoch')
    parser.add_argument('--lr_rate', default = 0.03, type=float, help='learning rate')
    parser.add_argument('--l2', default = 0.0, type=float, help='l2 normalization')
    parser.add_argument('--clip', default = 0.1, type=float, help='gradient clipping')
    parser.add_argument('--nlayer', default = 5, type=int, help='#layer')
    parser.add_argument('--nhidden', default = 320, type=int, help='dimesnion of hidden units in single direction')
    parser.add_argument('--nproj', default = 120, type=int, help='dimesnion of projection units in single direction, set to 0 if no projection needed')
    parser.add_argument('--half_period', default = 10, type=int, help='half period in epoch of learning rate')
    parser.add_argument('--temperature', default = 1, type=float, help='temperature used in softmax')
    parser.add_argument('--grad_opt', default = "grad", help='optimizer: grad, adam, momentum, cuddnn only work with grad')
    parser.add_argument('--log_dir', default = "/home/bchen2/Haitian/log", help='log path')
    parser.add_argument('--dataset', default = 'swbd', help='dataset selection: swbd, haitian')
    args = parser.parse_args()

    nclass, cv_data = load_feat(args, "cv")
    nfeat = cv_data[0][0].shape[-1]
    log_path = get_output_folder(args.log_dir)

    if args.eval:
        model_path = args.eval_model
        config_path = os.path.dirname(model_path) + "/config.pkl"
        config = pickle.load(open(config_path, "rb"))
        config["temperature"] = args.temperature
        config["prior"] = load_prior(args.count_dir)
        for k, v in config.items():
            print(k, v)
        sys.stdout.flush()
        tf.eval(cv_data, config, model_path)
    else:
        config = {
            "nfeat": nfeat, 
            "nclass": nclass, 
            "nepoch": args.nepoch,
            "lr_rate": args.lr_rate,
            "l2": args.l2,
            "clip": args.clip,
            "nlayer": args.nlayer, 
            "nhidden": args.nhidden, 
            "nproj": args.nproj,
            "cudnn": args.use_cudnn,
            "half_period": args.half_period,
            "grad_opt": args.grad_opt, 
            "batch_size": args.batch_size,
            "log_path": log_path,
            "store_model": args.store_model
        }
        for k, v in config.items():
            print(k, v)
        sys.stdout.flush()
        model_dir = config["log_path"] + "/model" 
        if not os.path.exists(model_dir):
            os.makedirs(model_dir)
        pickle.dump(config, open(log_path + "/model/config.pkl", "wb"))
        _, tr_data = load_feat(args, "train")
        cv_x, cv_y, _ = cv_data
        tr_x, tr_y, _ = tr_data
        data = (cv_x, tr_x, cv_y, tr_y)
        tf.train(data, config)

if __name__ == "__main__":
    main()
