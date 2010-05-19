"""
MCMC model types

Usage
-----

First create a :module:`bounds` object.  This stores the ranges available 
on the parameters, and controls how values outside the range are handled::

    M_bounds = bounds(minx, maxx, style='reflect|clip|fold|randomize|none')

For simple functions you can use one of the existing models.

If your model *f* computes the probability density, use :class:`Density`::

    M = Density(f, bounds=M_bounds)
    
If your model *f* computes the log probability density, use :class:`LogDensity`::

    M = LogDensity(f, bounds=M_bounds)

If your model *f* computes a simulation which returns a vector, and you
have *data* associated with the simulation, use :class:`Simulation`::

    M = Simulation(f, data=data, bounds=M_bounds)

The measurement *data* can have a 1-sigma uncertainty associated with it, as
well as a *gamma* factor if the uncertainty distribution has non-Gaussian
kurtosis associated with it.


Multivariate normal distribution::

    M = MVNormal(mu, sigma)

Mixture models::

    M = Mixture(M1, w1, M2, w2, ...)


For more complex functions, you can subclass MCMCModel::

    class Model(MCMCModel):
        def __init__(self, ..., bounds=None, ...):
            ...
            self.bounds = bounds
            ...
        def nnlf(self, x, RNG=numpy.random):
            "Return the negative log likelihood of seeing x"
            p = probability of seeing x
            return -log(p)

    M = Model(..., bounds=M_bounds, ...)

The parameter RNG is provided so that you have a source of random numbers
for your simulation.  The RNG you are called with will have the same
interface as numpy.random, but will [eventually] work correctly when
running parallel simulations on different machines with predefined seed values.

The MCMC program uses only two methods from the model::

    apply_bounds(pop, RNG)
    log_density(pop, RNG)

If your model provides these methods, you will not need to subclass MCMCModel
in order to interact with DREAM.


Compatibility with matlab DREAM
-------------------------------

First generate a bounds handling function::

    M_bounds = bounds(ParRange.minn, ParRange.maxn)

Then generate a model, depending on what kind of function you have.

Option 1. Model directly computes posterior density::

    model = Density(f, bounds=M_bounds)

Option 2. Model computes simulation, data has known 1-sigma uncertainty::

    model = Simulation(f, data=Measurement.MeasData, bounds=M_bounds,
                       sigma=Measurement.Sigma, gamma = MCMCPar.Gamma)

Option 3. Model computes simulation, data has unknown 1-sigma uncertainty::

    model = Simulation(f, data=Measurement.MeasData, bounds=M_bounds,
                       gamma = MCMCPar.Gamma)


Option 4. Model directly computes log posterior density::

    model = LogDensity(f, bounds=M_bounds)

Option 5 is like option 2 but the reported likelihoods do not take the
1-sigma uncertainty into account.  The metropolis steps are still based
on the 1-sigma uncertainty, so use the style given in option 2 for this case.

"""
from __future__ import division

__all__ = ['MCMCModel','Density','LogDensity','Simulation','MVNormal','Mixture']

from numpy import sum, ones_like, array
from numpy import dot, diag, log, exp, pi, asarray, isscalar
from numpy.linalg import cholesky, inv
import numpy.random
from . import exppow

class MCMCModel:
    """
    MCMCM model abstract base class.
    
    Each model must have a negative log likelihood function which operates
    on a point x, returning the negative log likelihood, or inf if the point
    is outside the domain.
    """
    labels = None
    bounds = None
    def nllf(self, x, RNG=numpy.random):
        raise NotImplemented
    def log_density(self, pop, RNG=numpy.random):
        return array([-self.nllf(x, RNG) for x in pop])
    def plot(self, x):
        pass

class Density(MCMCModel):
    """
    Construct an MCMC model from a probablility density function.
    
    *f* is the density function
    """
    def __init__(self, f, bounds=None, labels=None):
        self.f, self.bounds, self.labels = f, bounds, labels
    def nllf(self, x, RNG=numpy.random):
        return -log(self.f(x))

class LogDensity(MCMCModel):
    """
    Construct an MCMC model from a log probablility density function.
    
    *f* is the log density function
    """
    def __init__(self, f, bounds=None, labels=None):
        self.f, self.bounds, self.labels = f, bounds, labels
    def nllf(self, x, RNG=numpy.random):
        return -self.f(x)
 
class Simulation(MCMCModel):
    """
    Construct an MCMC model from a simulation function.

    *f* is the function which simulates the data
    *data* is the measurement(s) to compare it to
    *sigma* is the 1-sigma uncertainty of the measurement(s).
    *gamma* in (-1, 1] represents kurtosis on the data measurement uncertainty.

    Data is assumed to come from an exponential power density::
    
        p(v|S,G) = w(G)/S exp(-c(G) |v/S|^(2/(1+G)))

    where S is *sigma* and G is *gamma*.  

    The values of *sigma* and *gamma* can be uniform or can vary with the
    individual measurement points.

    Certain values of *gamma* select particular distributions::
        G = 0: normal
        G = 1: double exponential
        G -> -1: uniform
    """
    def __init__(self, f=None, bounds=None, data=None, sigma=1, gamma=0,
                 labels=None):
        self.f, self.bounds, self.labels = f, bounds, labels
        self.data, self.sigma, self.gamma = data, sigma, gamma
        cB, wB = exppow.exppow_pars(gamma)
        self._offset = sum(log(wB/sigma * ones_like(data)))
        self._cB = cB
        self._pow = 2/(1+gamma)
    def nllf(self, x, RNG=numpy.random):
        err = self.f(x) - self.data
        log_p = self._offset - sum(self._cB * abs(err/self.sigma)**self._pow)
        return -log_p
    def plot(self, x):
        import pylab
        v = pylab.arange(len(self.data))
        pylab.plot(v,self.data,'x',v,self.f(x),'-')


class MVNormal(MCMCModel):
    """
    multivariate normal negative log likelihood function
    """
    def __init__(self, mu, sigma):
        self.mu, self.sigma = asarray(mu), asarray(sigma)
        # Precompute sigma contributions
        r = cholesky(sigma)
        self._rinv = inv(r)
        self._C = 0.5*len(mu)*log(2*pi) + sum(diag(r))
    def nllf(self, x, RNG=numpy.random):
        mu,C,rinv = self.mu, self._C, self._rinv
        y = C + 0.5*sum( dot((x-mu), rinv)**2 )
        return y

class Mixture(MCMCModel):
    """
    Create a mixture model from a list of weighted density models.
    
    MixtureModel( M1, w1, M2, w2, ...)
    
    Models M1, M2, ... are MCMC models with M.nllf(x) returning the negative 
    log likelihood of x.  Weights w1, w2, ... are arbitrary scalars.
    """
    def __init__(self, *args):

        models = args[::2]
        weights = args[1::2]
        if (len(args)%2 != 0 
            or not all(hasattr(M,'nllf') for M in models)
            or not all(isscalar(w) for w in weights)):
            raise TypeError("Expected MixtureModel(M1,w1,M2,w2,...)")
        self.pairs = zip(models,weights)
        self.weight = sum(w for w in weights)

    def nllf(self, x, RNG=numpy.random):
        p = [w*exp(-M.nllf(x,RNG)) for M,w in self.pairs]
        return -log( sum(p)/self.weight )

