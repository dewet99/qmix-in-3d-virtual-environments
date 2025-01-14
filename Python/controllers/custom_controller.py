from models.icm_agent import ICMAgent
from components.action_selectors import EpsilonGreedyActionSelector, DiscreteNoisyGreedyActionSelector
import torch as th
import pdb
import os
from models.NatureVisualEncoder import NatureVisualEncoder
import traceback

# This multi-agent controller shares parameters between agents
class CustomMAC():
    def __init__(self, config, device = "cuda:0", encoder = None):
        super().__init__()
        self.n_agents = config["num_agents"]
        self.config = config
        # input_shape = self._get_input_shape()
        self.device = device
        self._build_agents()
        self.agent_output_type = config["agent_output_type"]

        self.encoder = encoder
        
        if self.config["action_selector"] == "epsilon_greedy":
            self.action_selector = EpsilonGreedyActionSelector(config)
        elif self.config["action_selector"] == "noisy":
            self.action_selector = DiscreteNoisyGreedyActionSelector(config)

        self.hidden_states = None



    def select_actions(self, ep_batch, t_ep, t_env, bs=slice(None), test_mode=False,):
        """
        Select actions from the agents using their networks
            flattened_observations should only be passed if a feature extraction network is used;
            they are the feature extracted observations; the original batch and RAW observations
            are not modified
            That way, the feature extracted observations used for training will improve in quality as
            training progresses and the feature extraction network is improved
        """

        
        # Only select actions for the selected batch elements in bs
        avail_actions = ep_batch["avail_actions"][:, t_ep]

        # Calculate agent Q-values for the step
        agent_outputs, _ = self.forward(ep_batch, t_ep, test_mode=test_mode)

        # do t_env+t_ep because t_env is only updated at the end of an episode
        chosen_actions = self.action_selector.select_action(agent_outputs[bs].to(self.device), avail_actions[bs].to(self.device), t_env, test_mode=test_mode)


        return chosen_actions
    
   
    def forward(self, ep_batch, t, test_mode=False, training=False):
        """
        returns:
            agent_outputs, hidden_states
        """
        inputs = self._build_inputs(ep_batch, t, training)

        agent_outs, self.hidden_states = self.agent(inputs, self.hidden_states.to(self.device), t, training)

        return agent_outs.view(ep_batch.batch_size, self.n_agents, -1), self.hidden_states
    

    
    def _build_inputs(self, batch, t, training = False):
        # Assumes homogenous agents with flat observations.
        # Other MACs might want to e.g. delegate building inputs to each agent
        bs = batch.batch_size
        inputs = []

        
        # Depends on how you defined your replay buffer observation storate dtype. I used integers to be able to store 
        # more of them, but dtype choice will depend on graphical fidelity of your environment.
        if batch["obs"][:,t].dtype == th.uint8:
            obs = (batch["obs"][:,t]).to(th.float32)/255
        else:
            obs = batch["obs"][:,t]

        try:
            feature = self.encoder(obs.squeeze())
            if training:
                feature = feature.reshape(bs, self.config["num_agents"], -1)
        except Exception as e:
            traceback.print_exc()


        # print(feature.shape)
        inputs.append(feature)  # b1av

        if self.config["obs_agent_id"]:
            inputs.append(th.eye(self.n_agents, device=batch.device).unsqueeze(0).expand(bs, -1, -1))

        inputs = th.cat([x.reshape(bs*self.n_agents, -1).to(self.device) for x in inputs], dim=1)

        return inputs.to(self.device)


    def init_hidden(self, batch_size, hidden_state):
        if self.config["use_burnin"] and hidden_state is not None:
            # if we init hidden based on a hidden state that was calculated in the forward pass
            self.hidden_states = hidden_state.expand(batch_size, self.n_agents, -1)
            # self.hidden_states = hidden_state
            
        else:
            self.hidden_states = self.agent.init_hidden().unsqueeze(0).expand(batch_size, self.n_agents, -1)  # bav
            # self.hidden_states = None
        # Non parameter sharing does the following in stead of the above: 
        # self.hidden_states = self.agent.init_hidden().unsqueeze(0).expand(batch_size, -1, -1)  # bav


    def parameters(self):
        return self.agent.parameters()
    
    
    def named_parameters(self):
        return self.agent.named_parameters()

    def load_state(self, other_mac):
        self.agent.load_state_dict(other_mac.agent.state_dict())
        self.encoder.load_state_dict(other_mac.encoder.state_dict())

    def cuda(self):
        self.agent.cuda()


    def save_models(self, path):
        th.save(self.agent.state_dict(), "{}/agent.th".format(path))

    def load_models(self, path):
        self.agent.load_state_dict(th.load("{}/agent.th".format(path), map_location=lambda storage, loc: storage))

    def _build_agents(self):
        self.agent = ICMAgent(self.config, self.device).to(self.device)

    # def _get_input_shape(self):
    #     # If there is an encoder, the input shape to the NN's is the output shape of the encoder
    #     input_shape = self.config["encoder_output_size"]
        
    #     if self.config["obs_agent_id"]:
    #         input_shape += self.n_agents

    #     return input_shape
    
    def reset_agent_noise(self):
        self.agent.reset_noise()
